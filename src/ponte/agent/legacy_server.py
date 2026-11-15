"""Servidor que fala o protocolo v1, montado com as peças novas do pacote (fase 1)."""

from __future__ import annotations

import ipaddress
import os
import socket
import socketserver
import threading
import time

from ..core import commands, legacy
from ..core.errors import CommandError, PonteError
from .execute import build_spec, run_collect

MAX_REQUEST_BYTES = 64 * 1024
MAX_ARGS = 32
MAX_ARG_CHARS = 4096
MAX_CLOCK_SKEW = 60
NONCE_TTL = 3 * MAX_CLOCK_SKEW
MAX_PARALLEL = 4


def log(msg: str) -> None:
    import sys

    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, file=sys.stderr, flush=True)


class NonceCache:
    def __init__(self, ttl: float, max_size: int = 100_000):
        self.ttl = ttl
        self.max_size = max_size
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()

    def add(self, nonce: str) -> bool:
        now = time.time()
        with self.lock:
            for n in [n for n, t in self.seen.items() if now - t > self.ttl]:
                del self.seen[n]
            if nonce in self.seen or len(self.seen) >= self.max_size:
                return False
            self.seen[nonce] = now
            return True


class Handler(socketserver.BaseRequestHandler):
    server: LegacyServer

    def handle(self) -> None:
        peer_ip = self.client_address[0]
        if not self.server.ip_allowed(peer_ip):
            log(f"recusado {peer_ip}, fora da lista de permitidos")
            return
        self.request.settimeout(15)
        try:
            req = legacy.recv_line(self.request, MAX_REQUEST_BYTES)
        except (PonteError, OSError) as e:
            log(f"pedido ilegível de {peer_ip} ({e})")
            return
        try:
            resp = self.server.process(req, peer_ip)
        except Exception as e:  # um pedido nunca derruba o servidor
            log(f"erro interno tratando pedido de {peer_ip} ({e!r})")
            # Só assina se o pedido estava autenticado (o v1 assinava sempre).
            resp = {"v": legacy.V1_PROTOCOL, "ok": False, "erro": "erro interno no servidor"}
            if legacy.verify(self.server.key, "req", req):
                resp["nonce"] = str(req.get("nonce", ""))[:32]
                resp["de"] = self.server.cfg["nome"]
                legacy.sign(self.server.key, "resp", resp)
        try:
            legacy.send_line(self.request, resp)
        except OSError as e:
            log(f"não consegui responder {peer_ip} ({e})")


class LegacyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, cfg: dict):
        if os.geteuid() == 0:
            raise PonteError("o agente não roda como root")
        self.cfg = cfg
        self.key: bytes = cfg["_key"]
        self.allowed = [ipaddress.ip_network(n, strict=False) for n in cfg["permitidos"]]
        self.nonces = NonceCache(NONCE_TTL)
        self.slots = threading.BoundedSemaphore(MAX_PARALLEL)
        host = cfg["escutar"]
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, int(cfg["porta"])), Handler)

    def ip_allowed(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.allowed)

    def process(self, req: dict, peer_ip: str) -> dict:
        if not legacy.verify(self.key, "req", req):
            log(f"assinatura inválida vinda de {peer_ip}")
            return {"v": legacy.V1_PROTOCOL, "ok": False, "erro": "assinatura invalida"}
        nonce = req.get("nonce")
        nonce = nonce if isinstance(nonce, str) else ""

        def reply(ok: bool, **fields) -> dict:
            body = {"v": legacy.V1_PROTOCOL, "nonce": nonce, "de": self.cfg["nome"], "ok": ok, **fields}
            return legacy.sign(self.key, "resp", body)

        if req.get("v") != legacy.V1_PROTOCOL:
            return reply(False, erro="versao de protocolo diferente, atualize o ponte nas duas maquinas")
        ts = req.get("ts")
        if (
            not isinstance(ts, (int, float))
            or isinstance(ts, bool)
            or not abs(time.time() - ts) <= MAX_CLOCK_SKEW
        ):
            return reply(False, erro="pedido velho ou relogios fora de sincronia")
        if not isinstance(nonce, str) or len(nonce) != 32 or not all(c in "0123456789abcdef" for c in nonce):
            return reply(False, erro="nonce invalido")
        if not self.nonces.add(nonce):
            log(f"pedido repetido vindo de {peer_ip}, ignorado")
            return reply(False, erro="pedido repetido")

        caller = str(req.get("de", "?"))[:64]
        action = req.get("acao")
        if action == "ping":
            return reply(True, hora=time.time(), versao=legacy.V1_PROTOCOL)
        if action == "listar":
            return reply(True, comandos=commands.list_commands())
        if action == "rodar":
            return reply(**self.run(req, caller, peer_ip))
        return reply(False, erro="acao desconhecida")

    def run(self, req: dict, caller: str, peer_ip: str) -> dict:
        name = req.get("comando")
        args = req.get("args", [])
        if not isinstance(name, str):
            return {"ok": False, "erro": "nome de comando invalido"}
        if (
            not isinstance(args, list)
            or len(args) > MAX_ARGS
            or not all(isinstance(a, str) and len(a) <= MAX_ARG_CHARS and "\x00" not in a for a in args)
        ):
            return {
                "ok": False,
                "erro": f"argumentos invalidos (maximo {MAX_ARGS}, cada um ate {MAX_ARG_CHARS} caracteres)",
            }
        try:
            resolved = commands.resolve(name)
        except CommandError as e:
            log(f"{caller}@{peer_ip} pediu '{name}' e foi recusado ({e})")
            return {"ok": False, "erro": legacy_text(str(e))}
        try:
            if args and not resolved.meta.aceita_args:
                return {
                    "ok": False,
                    "erro": f"'{name}' nao aceita argumentos (ponha ACEITA_ARGS = True no arquivo)",
                }
            if not self.slots.acquire(blocking=False):
                return {"ok": False, "erro": f"ja tem {MAX_PARALLEL} comandos rodando, tente de novo"}
            try:
                log(f"{caller}@{peer_ip} rodando {name} {args!r}")
                spec = build_spec(resolved, args, caller, resolved.meta.timeout)
                result, stdout, stderr = run_collect(spec)
            finally:
                self.slots.release()
        finally:
            resolved.close()
        if result.estourou_tempo:
            stderr += f"\n[ponte] passou de {resolved.meta.timeout}s e foi encerrado\n"
        log(f"{name} terminou com codigo {result.codigo} em {result.duracao:.2f}s")
        return {
            "ok": result.ok,
            "codigo": result.codigo,
            "stdout": stdout,
            "stderr": stderr,
            "truncado": result.truncado,
            "estourou_tempo": result.estourou_tempo,
            "duracao": result.duracao,
        }


_PLAIN = str.maketrans("áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ", "aaaaeeiooouucAAAAEEIOOOUUC")


def legacy_text(text: str) -> str:
    """O v1 escrevia sem acento. Mantém isso no modo v1 pra não quebrar scripts."""
    return text.translate(_PLAIN)
