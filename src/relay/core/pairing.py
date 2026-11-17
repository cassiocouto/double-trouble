"""Pareamento por código curto (ver docs/SEGURANCA.md, seção Pareamento).

Numa máquina (B):
    relay parear          abre uma janela de 2 minutos e mostra um código de 8 dígitos

Na outra (A):
    relay parear g15      pede o código e termina o pareamento

Um pareamento só libera as DUAS direções: A manda em B e B manda em A. Cada
direção ganha o seu próprio segredo.

Conversa, na porta de pareamento (padrão 47801), com frames do protocolo v2:

    A -> B   linha mágica RELAY-PAR/2
    B -> A   linha mágica RELAY-PAR/2
    A -> B   {"tipo": "ola", "de": A, "X": ...}
    B -> A   {"tipo": "ola", "de": B, "Y": ..., "confirmacao": cB}
    A -> B   {"tipo": "confirmacao", "confirmacao": cA, "porta": 47800, "mac": ...}
    B -> A   {"tipo": "pronto", "de": B, "porta": 47800, "mac": ...}

Resultado: dois segredos novos de 32 bytes (A -> B e B -> A), iguais dos dois
lados, que nunca passaram pela rede.
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
from . import protocol, spake2, tailscale
from .errors import AuthError, ConnectionFailed, ProtocolError, RelayError
from .tailscale import WhoisChecker

MAGIC = b"RELAY-PAR/2\n"
MAGIC_PREFIX = b"RELAY-PAR/"
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
        raise RelayError(f"o código tem {CODE_DIGITS} números, como 1234 5678")
    return digits


def _secret_label(controller: str, agent: str) -> str:
    return f"relay/2 segredo {controller} -> {agent}"


def _mac(key: bytes, obj: dict) -> str:
    return hmac.new(key, protocol.canonical(obj), hashlib.sha256).hexdigest()


def _read_magic(sock: socket.socket, deadline: float | None = None) -> bytes:
    return protocol.read_magic(sock, deadline, size=len(MAGIC))


# ==========================================================================
# lado do agente (B): mostra o código


@dataclass
class PairingResult:
    """O que a máquina que mostrou o código (B) aprende sobre a outra (A)."""

    controller: str  # nome de A
    secret: bytes  # segredo de A -> B (A manda aqui)
    reverse_secret: bytes  # segredo de B -> A (daqui manda em A)
    peer_port: int  # porta do agente de A
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
            raise RelayError(f"não consegui abrir a porta de pareamento em {host} ({e.strerror or e})") from None
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
            magic = _read_magic(conn, deadline)
            if magic != MAGIC:
                if magic.startswith(MAGIC_PREFIX):
                    conn.sendall(MAGIC)  # a outra ponta vê a nossa versão e explica
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
        # A prova de A conferiu. Agora a porta do agente de A, autenticada.
        peer_port = proof.get("porta")
        body = {k: v for k, v in proof.items() if k != "mac"}
        mac = proof.get("mac")
        if (
            not isinstance(mac, str)
            or not hmac.compare_digest(mac, _mac(pake.derive("relay/2 porta de A"), body))
            or not isinstance(peer_port, int)
            or isinstance(peer_port, bool)
            or not 0 < peer_port < 65536
        ):
            self._fail(conn, "confirmação inválida", ip)
            return None
        secret = pake.derive(_secret_label(name, self.cfg.name))
        reverse = pake.derive(_secret_label(self.cfg.name, name))
        done = {"tipo": "pronto", "de": self.cfg.name, "porta": self.cfg.agent.port}
        done["mac"] = _mac(pake.derive("relay/2 pronto"), done)
        with contextlib.suppress(OSError, ProtocolError):
            protocol.send_frame(conn, done)
        self.on_event(f"pareado com {name} ({ip})")
        return PairingResult(controller=name, secret=secret, reverse_secret=reverse, peer_port=peer_port, peer_ip=ip)


def save_both_directions(
    other: str,
    address: str,
    port: int,
    outgoing: bytes,
    incoming: bytes,
    path=None,
) -> config_mod.Config:
    """Grava as duas direções: esta manda na outra (outgoing) e a outra manda nesta (incoming).

    Re-parear troca os segredos e mantém as permissões de comandos que já existiam.
    """

    def change(cfg: config_mod.Config) -> None:
        cfg.machines[other] = config_mod.MachineEntry(name=other, address=address, port=port, secret=outgoing)
        old = cfg.agent.controllers.get(other)
        cfg.agent.controllers[other] = config_mod.ControllerEntry(
            name=other,
            secret=incoming,
            commands=old.commands if old else None,
            tailscale_node=old.tailscale_node if old else None,
        )

    return config_mod.update(change, path)


def save_result(result: PairingResult, path=None) -> config_mod.Config:
    """Lado que mostrou o código. O endereço da outra é o nome dela no Tailscale
    (ou, se não der pra achar, o IP de onde o pareamento veio)."""
    try:
        address = tailscale.resolve_peer(result.controller)
    except RelayError:
        address = None
    address = address or result.peer_ip.split("%", 1)[0]
    return save_both_directions(
        result.controller, address, result.peer_port, result.reverse_secret, result.secret, path
    )


# ==========================================================================
# lado do controlador (A): digita o código


@dataclass
class PairedMachine:
    """O que a máquina que digitou o código (A) aprende sobre a outra (B)."""

    name: str
    address: str
    port: int
    secret: bytes  # A -> B (daqui manda em B)
    reverse_secret: bytes  # B -> A (B manda aqui)


def pair_with(
    my_name: str, address: str, port: int, code: str, my_port: int, timeout: float = CONN_TIMEOUT
) -> PairedMachine:
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
            return _pair_conversation(sock, my_name, address, code, my_port)
        except TimeoutError:
            raise ConnectionFailed(f"{address} parou de responder no meio do pareamento") from None
        except OSError as e:
            raise ConnectionFailed(f"a conexão com {address} caiu ({e})") from None


def _pair_conversation(sock: socket.socket, my_name: str, address: str, code: str, my_port: int) -> PairedMachine:
    sock.sendall(MAGIC)
    answer = _read_magic(sock)
    if answer != MAGIC:
        if answer.startswith(MAGIC_PREFIX):
            raise ProtocolError(
                f"{address} tem outra versão do relay. Rode git pull e o scripts/install.sh nas duas máquinas"
            )
        raise ProtocolError(f"{address} não está com uma janela de pareamento do relay aberta")
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
    proof = {"tipo": "confirmacao", "confirmacao": pake.my_confirmation(), "porta": my_port}
    proof["mac"] = _mac(pake.derive("relay/2 porta de A"), proof)
    protocol.send_frame(sock, proof)
    done = protocol.recv_frame(sock, MAX_FRAME)
    if done.get("tipo") == "erro":
        raise AuthError(f"a outra máquina recusou ({str(done.get('erro'))[:200]})")
    mac = done.get("mac")
    body = {k: v for k, v in done.items() if k != "mac"}
    expected = _mac(pake.derive("relay/2 pronto"), body)
    if done.get("tipo") != "pronto" or not isinstance(mac, str) or not hmac.compare_digest(mac, expected):
        raise AuthError("a confirmação final do pareamento veio errada")
    port = done.get("porta")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ProtocolError("porta inválida na resposta do pareamento")
    return PairedMachine(
        name=agent,
        address=address,
        port=port,
        secret=pake.derive(_secret_label(my_name, agent)),
        reverse_secret=pake.derive(_secret_label(agent, my_name)),
    )


def save_paired(paired: PairedMachine, path=None) -> config_mod.Config:
    """Lado que digitou o código."""
    return save_both_directions(paired.name, paired.address, paired.port, paired.secret, paired.reverse_secret, path)


def pair_and_save(target: str, code: str, port: int | None = None, path=None) -> PairedMachine:
    """Atalho pra CLI e interface. `target` pode ser só um pedaço do nome (g15, hp)."""
    cfg = config_mod.load(path)
    address = tailscale.resolve_peer(target) or target
    paired = pair_with(cfg.name, address, port or config_mod.DEFAULT_PORT + 1, code, cfg.agent.port)
    save_paired(paired, path)
    return paired
