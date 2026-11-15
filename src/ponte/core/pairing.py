"""Pareamento por código curto (ver docs/SEGURANCA.md, seção Pareamento).

Na máquina que vai EXECUTAR (o agente, B):
    ponte parear              abre uma janela de 2 minutos e mostra um código de 8 dígitos

Na máquina que vai MANDAR (o controlador, A):
    ponte parear --com g15    pede o código e termina o pareamento

Conversa, na porta de pareamento (padrão 47801), com frames do protocolo v2:

    A -> B   linha mágica PONTE-PAR/1
    B -> A   linha mágica PONTE-PAR/1
    A -> B   {"tipo": "ola", "de": A, "X": ...}
    B -> A   {"tipo": "ola", "de": B, "Y": ..., "confirmacao": cB}
    A -> B   {"tipo": "confirmacao", "confirmacao": cA}
    B -> A   {"tipo": "pronto", "porta": 47800, "mac": ...}

Resultado: um segredo novo de 32 bytes, igual dos dois lados, que nunca
passou pela rede. Ele vale só pra direção A -> B.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import config as config_mod
from . import protocol, spake2
from .errors import AuthError, ConnectionFailed, PonteError, ProtocolError
from .tailscale import WhoisChecker

MAGIC = b"PONTE-PAR/1\n"
WINDOW_SECONDS = 120
MAX_FAILURES = 3
CODE_DIGITS = 8
MAX_FRAME = 4096
CONN_TIMEOUT = 10


def generate_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def format_code(code: str) -> str:
    return f"{code[:4]} {code[4:]}"


def normalize_code(text: str) -> str:
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) != CODE_DIGITS or len(digits) != len("".join(c for c in text if not c.isspace() and c != "-")):
        raise PonteError(f"o código tem {CODE_DIGITS} números, como 1234 5678")
    return digits


def _secret_label(controller: str, agent: str) -> str:
    return f"ponte/2 segredo {controller} -> {agent}"


def _mac(key: bytes, obj: dict) -> str:
    return hmac.new(key, protocol.canonical(obj), hashlib.sha256).hexdigest()


def _read_magic(sock: socket.socket, deadline: float | None = None) -> bytes:
    return protocol.read_magic(sock, deadline, size=len(MAGIC))


# ==========================================================================
# lado do agente (B): mostra o código


@dataclass
class PairingResult:
    controller: str
    secret: bytes
    peer_ip: str


class PairingWindow:
    """Escuta por um tempo curto e aceita um pareamento com o código certo."""

    def __init__(
        self,
        cfg: config_mod.Config,
        host: str,
        port: int | None = None,
        code: str | None = None,
        seconds: float = WINDOW_SECONDS,
        max_failures: int = MAX_FAILURES,
        whois: WhoisChecker | None = None,
        on_event: Callable[[str], None] | None = None,
    ):
        self.cfg = cfg
        self.host = host
        self.code = code or generate_code()
        self.seconds = seconds
        self.max_failures = max_failures
        self.whois = whois or WhoisChecker()
        self.on_event = on_event or (lambda _m: None)
        self.failures = 0
        self.deadline = 0.0
        self._stopped = False
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((host, cfg.agent.pairing_port if port is None else port))
        except OSError as e:
            self.sock.close()
            raise PonteError(f"não consegui abrir a porta de pareamento em {host} ({e.strerror or e})") from None
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.networks = cfg.agent.networks()

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic()) if self.deadline else self.seconds

    def stop(self) -> None:
        self._stopped = True
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.sock.close()

    def _allowed(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in n for n in self.networks)

    def serve(self) -> PairingResult | None:
        """Bloqueia até parear (devolve o resultado) ou até a janela fechar (None)."""
        self.deadline = time.monotonic() + self.seconds
        try:
            while not self._stopped:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    self.on_event("o tempo acabou")
                    return None
                self.sock.settimeout(min(left, 1.0))
                try:
                    conn, addr = self.sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return None  # stop() fechou o socket
                ip = addr[0]
                with conn:
                    if not self._allowed(ip):
                        self.on_event(f"recusei {ip}, fora da lista de permitidos")
                        continue
                    result = self._handle(conn, ip)
                if result is not None:
                    return result
                if self.failures >= self.max_failures:
                    self.on_event(f"{self.failures} tentativas erradas, fechei o pareamento")
                    return None
            return None
        finally:
            self.stop()

    def _fail(self, conn: socket.socket, why: str, ip: str) -> None:
        self.failures += 1
        self.on_event(f"tentativa recusada de {ip}: {why}")
        with contextlib.suppress(OSError, ProtocolError):
            protocol.send_frame(conn, {"tipo": "erro", "erro": why})

    def _handle(self, conn: socket.socket, ip: str) -> PairingResult | None:
        # Prazo total por conexão: a janela atende uma conexão por vez, então uma
        # conexão lenta de propósito não pode segurar o pareamento.
        deadline = time.monotonic() + CONN_TIMEOUT
        conn.settimeout(CONN_TIMEOUT)
        try:
            if _read_magic(conn, deadline) != MAGIC:
                return None
            conn.sendall(MAGIC)
            hello = protocol.recv_frame(conn, MAX_FRAME, deadline)
        except (OSError, ProtocolError):
            return None
        name = hello.get("de")
        if hello.get("tipo") != "ola" or not isinstance(name, str) or not config_mod.MACHINE_NAME_RE.fullmatch(name):
            self._fail(conn, "mensagem inválida", ip)
            return None
        if name == self.cfg.name:
            self._fail(conn, "a máquina não pode parear com ela mesma pelo mesmo nome", ip)
            return None
        try:
            self.whois.check(self.cfg.agent.verify_tailscale, ip, name)
        except AuthError as e:
            self._fail(conn, str(e), ip)
            return None
        pake = spake2.Spake2("B", self.code, name, self.cfg.name)
        try:
            pake.finish(hello.get("X"))
        except ValueError:
            self._fail(conn, "mensagem inválida", ip)
            return None
        try:
            protocol.send_frame(
                conn,
                {"tipo": "ola", "de": self.cfg.name, "Y": pake.message(), "confirmacao": pake.my_confirmation()},
            )
            proof = protocol.recv_frame(conn, MAX_FRAME, deadline)
        except (OSError, ProtocolError):
            self.failures += 1  # sumiu no meio: conta, pra ninguém ficar tentando à toa
            return None
        if proof.get("tipo") != "confirmacao" or not pake.check_peer(proof.get("confirmacao")):
            self._fail(conn, "código errado", ip)
            return None
        secret = pake.derive(_secret_label(name, self.cfg.name))
        done = {"tipo": "pronto", "de": self.cfg.name, "porta": self.cfg.agent.port}
        done["mac"] = _mac(pake.derive("ponte/2 pronto"), done)
        with contextlib.suppress(OSError, ProtocolError):
            protocol.send_frame(conn, done)
        self.on_event(f"pareado com {name} ({ip})")
        return PairingResult(controller=name, secret=secret, peer_ip=ip)


def save_controller(result: PairingResult, path=None) -> config_mod.Config:
    """Grava o controlador novo no config do agente. Re-parear mantém as permissões."""

    def change(cfg: config_mod.Config) -> None:
        old = cfg.agent.controllers.get(result.controller)
        cfg.agent.controllers[result.controller] = config_mod.ControllerEntry(
            name=result.controller,
            secret=result.secret,
            commands=old.commands if old else None,
            tailscale_node=old.tailscale_node if old else None,
        )

    return config_mod.update(change, path)


# ==========================================================================
# lado do controlador (A): digita o código


@dataclass
class PairedMachine:
    name: str
    address: str
    port: int
    secret: bytes


def pair_with(my_name: str, address: str, port: int, code: str, timeout: float = CONN_TIMEOUT) -> PairedMachine:
    """Faz o lado do controlador. Devolve o segredo novo (ainda não gravado)."""
    code = normalize_code(code)
    try:
        sock = socket.create_connection((address, port), timeout=timeout)
    except socket.gaierror:
        raise ConnectionFailed(f"não achei o endereço '{address}'. O Tailscale está ligado?") from None
    except OSError as e:
        raise ConnectionFailed(
            f"não consegui falar com {address}:{port} ({e.strerror or e}). O pareamento está aberto lá?"
        ) from None
    with sock:
        sock.settimeout(timeout)
        try:
            return _pair_conversation(sock, my_name, address, code)
        except TimeoutError:
            raise ConnectionFailed(f"{address} parou de responder no meio do pareamento") from None
        except OSError as e:
            raise ConnectionFailed(f"a conexão com {address} caiu ({e})") from None


def _pair_conversation(sock: socket.socket, my_name: str, address: str, code: str) -> PairedMachine:
    sock.sendall(MAGIC)
    if _read_magic(sock) != MAGIC:
        raise ProtocolError(f"{address} não está com uma janela de pareamento do ponte aberta")
    # X não depende dos nomes. O nome do agente só entra no fim (no TT), então dá
    # pra mandar o nosso lado antes de saber como o agente se chama.
    pake = spake2.Spake2("A", code, my_name, "")
    protocol.send_frame(sock, {"tipo": "ola", "de": my_name, "X": pake.message()})
    hello = protocol.recv_frame(sock, MAX_FRAME)
    if hello.get("tipo") == "erro":
        raise AuthError(f"a outra máquina recusou ({str(hello.get('erro'))[:200]})")
    agent = hello.get("de")
    if hello.get("tipo") != "ola" or not isinstance(agent, str) or not config_mod.MACHINE_NAME_RE.fullmatch(agent):
        raise ProtocolError("resposta de pareamento inválida")
    pake.id_b = agent.encode("utf-8")
    try:
        pake.finish(hello.get("Y"))
    except ValueError:
        raise ProtocolError("resposta de pareamento inválida") from None
    if not pake.check_peer(hello.get("confirmacao")):
        # Não manda a nossa confirmação: o outro lado não ganha nada pra testar.
        raise AuthError("código errado (ou a janela de pareamento já fechou). Abra de novo e confira os números")
    protocol.send_frame(sock, {"tipo": "confirmacao", "confirmacao": pake.my_confirmation()})
    done = protocol.recv_frame(sock, MAX_FRAME)
    if done.get("tipo") == "erro":
        raise AuthError(f"a outra máquina recusou ({str(done.get('erro'))[:200]})")
    mac = done.get("mac")
    body = {k: v for k, v in done.items() if k != "mac"}
    expected = _mac(pake.derive("ponte/2 pronto"), body)
    if done.get("tipo") != "pronto" or not isinstance(mac, str) or not hmac.compare_digest(mac, expected):
        raise AuthError("a confirmação final do pareamento veio errada")
    port = done.get("porta")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ProtocolError("porta inválida na resposta do pareamento")
    secret = pake.derive(_secret_label(my_name, agent))
    return PairedMachine(name=agent, address=address, port=port, secret=secret)


def save_machine(paired: PairedMachine, path=None) -> config_mod.Config:
    def change(cfg: config_mod.Config) -> None:
        cfg.machines[paired.name] = config_mod.MachineEntry(
            name=paired.name, address=paired.address, port=paired.port, secret=paired.secret
        )

    return config_mod.update(change, path)


def pair_and_save(address: str, port: int, code: str, path=None) -> PairedMachine:
    """Atalho pra CLI e interface: pareia e grava no config deste controlador."""
    cfg = config_mod.load(path)
    paired = pair_with(cfg.name, address, port, code)
    save_machine(paired, path)
    return paired
