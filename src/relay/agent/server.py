"""Servidor do agente (protocolo v2).

Ordem das checagens num pedido
  1. IP na lista de permitidos, antes de ler qualquer byte (invariante 5)
  2. limite de conexões abertas
  3. linha mágica (versão)
  4. um frame de até 64 KiB (invariante 10)
  5. controlador conhecido e assinatura válida. Se falhar, erro sem assinatura
  6. destino, horário e nonce. Daqui pra frente todo erro é assinado
  7. tailscale whois, se ligado
  8. permissão do controlador pro comando, metadados, parâmetros
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import __version__
from ..core import commands, protocol
from ..core.config import Config, ConfigSource, ControllerEntry
from ..core.errors import AuthError, CommandError, ParamError, ProtocolError, RelayError
from ..core.media import ARTIFACT_CHUNK
from ..core.params import validate_values
from ..core.tailscale import WhoisChecker
from .execute import build_spec
from .jobs import Busy, Job, JobManager

MAX_CONNECTIONS = 32
MAX_PER_IP = 4  # um IP sozinho não ocupa todas as vagas
UNAUTH_AUDIT_PER_MINUTE = 30  # recusas de quem não se autenticou que vão pra auditoria, por minuto
READ_TIMEOUT = 15  # prazo total pra receber a linha mágica e o pedido
SEND_TIMEOUT = 15  # cada envio de resposta
HEARTBEAT = 10
MAX_ARGS = 32
MAX_ARG_CHARS = 4096
MAX_HISTORY = 50
JOB_ID_MAX = 32

V1_REPLY = (
    json.dumps(
        {
            "v": 2,
            "ok": False,
            "erro": "este agente fala o protocolo v2. Atualize o relay nas duas maquinas",
        }
    ).encode()
    + b"\n"
)


_CONTROL = {c: f"\\x{c:02x}" for c in [*range(32), 127]}


def clean(text: object, limit: int = 200) -> str:
    """Texto que pode ter vindo de fora: sem quebra de linha nem controle, e curto."""
    out = str(text).translate(_CONTROL)
    return out if len(out) <= limit else out[:limit] + "…"


def log(msg: str) -> None:
    # Escapa controle: ninguém de fora consegue forjar uma linha nova no journal.
    print(time.strftime("%Y-%m-%d %H:%M:%S"), clean(msg, 1000), file=sys.stderr, flush=True)


AuditHook = Callable[[dict], None]


class Handler(socketserver.BaseRequestHandler):
    server: AgentServer

    def handle(self) -> None:
        try:
            self._handle()
        except Exception as e:  # um pedido nunca derruba o agente (invariante 11)
            log(f"erro inesperado com {self.client_address[0]} ({e!r})")

    def _handle(self) -> None:
        srv = self.server
        sock: socket.socket = self.request
        peer_ip = self.client_address[0]
        # Prazo TOTAL pra linha mágica + pedido. Sem isso, quem manda um byte a
        # cada 14 s seguraria a conexão pra sempre (e são só 32 vagas).
        deadline = time.monotonic() + READ_TIMEOUT
        try:
            magic = protocol.read_magic(sock, deadline)
        except OSError:
            return
        if magic == b"{":
            # Cliente v1 (linha JSON). Responde no formato dele, sem assinatura.
            sock.sendall(V1_REPLY)
            srv.audit_event("versao_diferente", peer_ip, detalhe="cliente v1")
            return
        if magic != protocol.MAGIC:
            if magic.startswith(protocol.MAGIC_PREFIX):
                sock.sendall(protocol.MAGIC)  # o cliente vê a nossa versão e explica
                srv.audit_event("versao_diferente", peer_ip, detalhe=magic[:16].decode("ascii", "replace"))
            return
        sock.sendall(protocol.MAGIC)

        try:
            req = protocol.recv_frame(sock, protocol.MAX_REQUEST_FRAME, deadline)
        except ProtocolError as e:
            log(f"pedido ilegível de {peer_ip} ({e})")
            srv.audit_event("pedido_ilegivel", peer_ip, detalhe=str(e))
            self._unsigned(sock, "pedido ilegível")
            return
        except OSError as e:
            log(f"{peer_ip} não mandou o pedido a tempo ({e})")
            return
        sock.settimeout(SEND_TIMEOUT)

        cfg = srv.config_source.get()
        if cfg is None:
            self._unsigned(sock, "o agente está com o config quebrado. Veja os logs da outra máquina")
            return
        try:
            entry, key = srv.authenticate(cfg, req)
        except AuthError:
            log(f"assinatura inválida ou máquina desconhecida vinda de {peer_ip}")
            srv.audit_event("nao_autenticado", peer_ip, de=str(req.get("de"))[:64])
            self._unsigned(sock, protocol.UNAUTHENTICATED)
            return

        nonce = req["nonce"] if isinstance(req.get("nonce"), str) else ""
        out = protocol.Responder(sock=sock, key=key, agent=cfg.name, controller=entry.name, nonce=nonce)
        try:
            srv.dispatch(cfg, entry, req, out, peer_ip)
        except (OSError, ProtocolError) as e:
            log(f"conexão com {entry.name}@{peer_ip} caiu ({e})")
        except Exception as e:
            log(f"erro interno com pedido de {entry.name}@{peer_ip} ({e!r})")
            if not out.closed:
                try:
                    out.error("erro interno no agente", "interno")
                except OSError:
                    pass

    def _unsigned(self, sock: socket.socket, message: str) -> None:
        try:
            protocol.send_frame(sock, protocol.unsigned_error(message))
        except (OSError, ProtocolError):
            pass


class AgentServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(
        self,
        config_source: ConfigSource,
        host: str,
        port: int,
        jobs: JobManager | None = None,
        audit: AuditHook | None = None,
        whois: WhoisChecker | None = None,
        base_dir=None,
    ):
        if os.geteuid() == 0 or os.getuid() == 0:
            raise RelayError("o agente não roda como root. Rode como seu usuário (systemctl --user)")
        self.config_source = config_source
        cfg = config_source.get()
        if cfg is None:
            raise RelayError(config_source.error or "config inválido")
        self.jobs = jobs or JobManager(max_parallel=cfg.agent.max_parallel)
        self.audit = audit
        self.whois = whois or WhoisChecker()
        self.base_dir = base_dir
        self.nonces = protocol.NonceCache()
        self.conn_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._per_ip: dict[str, int] = {}
        self._per_ip_lock = threading.Lock()
        self._unauth_window = (0.0, 0)  # (início do minuto, quantos já foram gravados)
        self._unauth_dropped = 0
        self._unauth_lock = threading.Lock()
        self._dummy_key = os.urandom(32)
        self.started = time.time()
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, port), Handler)

    # -- conexões ---------------------------------------------------------
    def ip_allowed(self, ip: str) -> bool:
        cfg = self.config_source.get()
        if cfg is None:
            return False
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in cfg.agent.networks())

    def verify_request(self, request, client_address) -> bool:
        """Chamado logo depois do accept, antes de ler qualquer coisa."""
        ip = client_address[0]
        if not self.ip_allowed(ip):
            log(f"recusado {ip}, fora da lista de permitidos")
            self.audit_event("ip_recusado", ip)
            return False
        with self._per_ip_lock:
            if self._per_ip.get(ip, 0) >= MAX_PER_IP:
                log(f"recusado {ip}, já tem {MAX_PER_IP} conexões abertas desse IP")
                return False
            if not self.conn_slots.acquire(blocking=False):
                log(f"recusado {ip}, conexões demais abertas")
                return False
            self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        return True

    def _release(self, ip: str) -> None:
        with self._per_ip_lock:
            left = self._per_ip.get(ip, 1) - 1
            if left > 0:
                self._per_ip[ip] = left
            else:
                self._per_ip.pop(ip, None)
            self.conn_slots.release()

    def process_request(self, request, client_address):
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release(client_address[0])
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(client_address[0])

    def handle_error(self, request, client_address):  # pragma: no cover - Handler já trata tudo
        log(f"erro tratando conexão de {client_address[0]}")

    # -- autenticação -----------------------------------------------------
    def authenticate(self, cfg: Config, req: dict) -> tuple[ControllerEntry, bytes]:
        """Confere quem mandou e a assinatura. Qualquer falha vira o mesmo erro genérico."""
        de = req.get("de")
        entry = cfg.agent.controllers.get(de) if isinstance(de, str) else None
        # Nome desconhecido faz a mesma conta (com uma chave qualquer), pra o tempo
        # de resposta não contar quais nomes existem.
        secret = entry.secret if entry is not None else self._dummy_key
        key = protocol.derive_key(secret, de if isinstance(de, str) else "", cfg.name)
        valid = protocol.verify(key, protocol.KIND_REQUEST, req)
        if entry is None or not valid:
            raise AuthError(protocol.UNAUTHENTICATED)
        return entry, key

    UNAUTH_EVENTS = ("ip_recusado", "nao_autenticado", "pedido_ilegivel", "versao_diferente")

    def _unauth_allowed(self) -> bool:
        """No máximo UNAUTH_AUDIT_PER_MINUTE recusas anônimas por minuto vão pro arquivo.

        Assim ninguém de fora apaga o histórico da auditoria só mandando lixo.
        """
        now = time.monotonic()
        with self._unauth_lock:
            start, count = self._unauth_window
            if now - start >= 60:
                dropped = self._unauth_dropped
                self._unauth_window, self._unauth_dropped = (now, 1), 0
            elif count >= UNAUTH_AUDIT_PER_MINUTE:
                self._unauth_dropped += 1
                return False
            else:
                self._unauth_window = (start, count + 1)
                return True
        if dropped and self.audit:
            self.audit({"evento": "recusas_suprimidas", "quantas": dropped})
        return True

    def audit_event(self, event: str, ip: str, **fields) -> None:
        if self.audit is None:
            return
        if event in self.UNAUTH_EVENTS and not self._unauth_allowed():
            return
        for key in ("detalhe", "de", "comando"):
            if key in fields and isinstance(fields[key], str):
                fields[key] = clean(fields[key])
        try:
            self.audit({"evento": event, "ip": ip, **fields})
        except Exception as e:
            log(f"falha gravando auditoria ({e!r})")

    # -- pedidos autenticados ---------------------------------------------
    def dispatch(self, cfg: Config, entry: ControllerEntry, req: dict, out: protocol.Responder, ip: str) -> None:
        if req.get("v") != protocol.PROTOCOL:
            return out.error("versão de protocolo diferente. Atualize o relay nas duas máquinas", "versao")
        if req.get("para") != cfg.name:
            return out.error(f"este pedido era pra '{req.get('para')}', aqui é '{cfg.name}'", "destino")
        ts = req.get("ts")
        if (
            not isinstance(ts, (int, float))
            or isinstance(ts, bool)
            or not abs(time.time() - ts) <= protocol.MAX_CLOCK_SKEW
        ):
            return out.error(
                "pedido velho ou relógios fora de sincronia (diferença maior que "
                f"{protocol.MAX_CLOCK_SKEW} s). Confira o relógio das duas máquinas",
                "relogio",
            )
        nonce = req.get("nonce")
        if not isinstance(nonce, str) or not protocol.NONCE_RE.fullmatch(nonce):
            return out.error("nonce inválido", "nonce")
        if not self.nonces.add(nonce):
            log(f"pedido repetido de {entry.name}@{ip}, ignorado")
            self.audit_event("repetido", ip, de=entry.name)
            return out.error("pedido repetido", "repetido")
        try:
            self.whois.check(cfg.agent.verify_tailscale, ip, entry.tailscale_node or entry.name)
        except AuthError as e:
            log(f"{entry.name}@{ip} falhou na checagem do tailscale ({e})")
            self.audit_event("whois_recusado", ip, de=entry.name, detalhe=str(e))
            return out.error(str(e), "whois")

        action = req.get("acao")
        handler = {
            "ping": self.do_ping,
            "listar": self.do_list,
            "rodar": self.do_run,
            "cancelar": self.do_cancel,
            "status": self.do_status,
            "historico": self.do_history,
        }.get(action if isinstance(action, str) else "")
        if handler is None:
            return out.error("ação desconhecida", "acao")
        return handler(cfg, entry, req, out, ip)

    def do_ping(self, cfg, entry, req, out, ip) -> None:
        out.send(
            "pong",
            fim=True,
            hora=time.time(),
            nome=cfg.name,
            versao=__version__,
            protocolo=protocol.PROTOCOL,
            jobs_rodando=self.jobs.running_count,
            ligado_desde=self.started,
        )

    def do_list(self, cfg, entry, req, out, ip) -> None:
        items = [c for c in commands.list_commands(self.base_dir) if entry.allows(c["nome"])]
        try:
            out.send("comandos", fim=True, comandos=items)
        except ProtocolError:
            out.error("a lista de comandos ficou grande demais pra uma mensagem", "grande")

    def do_run(self, cfg, entry: ControllerEntry, req, out, ip) -> None:
        name = req.get("comando")
        args = req.get("args", [])
        params = req.get("parametros")
        follow = req.get("seguir", True)
        if not commands.valid_name(name):
            return out.error("nome de comando inválido", "nome")
        if not entry.allows(name):
            self.audit_event("sem_permissao", ip, de=entry.name, comando=name)
            return out.error(f"esta máquina não deixa '{entry.name}' rodar '{name}'", "permissao")
        if not isinstance(follow, bool):
            return out.error("'seguir' precisa ser true ou false", "pedido")
        if (
            not isinstance(args, list)
            or len(args) > MAX_ARGS
            or not all(isinstance(a, str) and len(a) <= MAX_ARG_CHARS and "\x00" not in a for a in args)
        ):
            return out.error(
                f"argumentos inválidos (no máximo {MAX_ARGS}, cada um até {MAX_ARG_CHARS} caracteres, sem byte nulo)",
                "args",
            )
        try:
            resolved = commands.resolve(name, self.base_dir)
        except CommandError as e:
            log(f"{entry.name}@{ip} pediu '{name}' e foi recusado ({e})")
            self.audit_event("recusado", ip, de=entry.name, comando=name, detalhe=str(e))
            return out.error(str(e), "comando")
        try:
            meta = resolved.meta
            if meta.confirmar and req.get("confirmado") is not True:
                # Não é barreira contra quem pegou o controlador (ele mandaria true). Garante
                # que todo controlador pergunte antes, inclusive scripts e versões futuras.
                return out.error(f"'{name}' pede confirmação: o controlador precisa perguntar antes", "confirmar")
            stdin_data = None
            clean_params = None
            if meta.parametros is not None:
                if args:
                    return out.error(f"'{name}' usa PARAMETROS, mande os valores em 'parametros'", "args")
                try:
                    clean_params = validate_values(meta.parametros, params)
                except ParamError as e:
                    return out.error(str(e), "parametro")
                stdin_data = json.dumps(clean_params, ensure_ascii=False).encode("utf-8")
            else:
                if params not in (None, {}):
                    return out.error(f"'{name}' não declara PARAMETROS", "parametro")
                if args and not meta.aceita_args:
                    return out.error(f"'{name}' não aceita argumentos (ponha ACEITA_ARGS = True no arquivo)", "args")
            # Pasta temporária onde o comando pode escrever uma imagem/PDF que
            # volta pro controlador. O job a apaga quando termina.
            art_dir = tempfile.mkdtemp(prefix="relay-saida-")
            art_file = str(Path(art_dir) / "saida")
            spec = build_spec(resolved, args, entry.name, meta.timeout, stdin_data=stdin_data, output_file=art_file)
            shown = _redact(meta.parametros, clean_params)  # o job guarda só a versão pra log
            try:
                job = self.jobs.start(entry.name, name, spec, shown, args, artifact_file=art_file, artifact_dir=art_dir)
            except Busy as e:
                _cleanup(art_dir)
                return out.error(str(e), "ocupado")
        finally:
            resolved.close()
        log(f"{entry.name}@{ip} rodando {name} (job {job.id})")
        self.audit_event(
            "rodar",
            ip,
            de=entry.name,
            comando=name,
            job=job.id,
            parametros=shown,
            args=[a[:200] for a in args],
        )
        out.send("aceito", fim=not follow, job=job.id, comando=name, timeout=meta.timeout)
        if follow:
            self.stream(job, out, 0)

    def stream(self, job: Job, out: protocol.Responder, index: int, follow: bool = True) -> None:
        """Manda a saída a partir de `index` e, com follow, ao vivo até o job acabar.

        Se a conexão cair, o job continua (com o timeout dele) e o resultado
        fica no histórico.
        """
        last = time.monotonic()
        while True:
            chunks, index, done = job.wait_new(index, timeout=0.25 if follow else 0)
            for stream_name, text in _coalesce(chunks):
                out.send("saida", job=job.id, fluxo=stream_name, dados=text)
                last = time.monotonic()
            if done or not follow:
                break
            if not chunks and time.monotonic() - last >= HEARTBEAT:
                out.send("vivo", job=job.id)
                last = time.monotonic()
            if chunks:
                time.sleep(0.05)  # deixa juntar mais saída num frame só
        self._send_artifact(job, out)
        out.send("fim", fim=True, job=job.id, resultado=job.result_dict())

    def _send_artifact(self, job: Job, out: protocol.Responder) -> None:
        """Manda a imagem/PDF (se o comando escreveu uma) em pedaços antes do fim."""
        art = job.artifact
        if art is None:
            return
        data = art.data
        total = (len(data) + ARTIFACT_CHUNK - 1) // ARTIFACT_CHUNK
        for i in range(total):
            pedaco = data[i * ARTIFACT_CHUNK : (i + 1) * ARTIFACT_CHUNK]
            out.send(
                "artefato",
                job=job.id,
                tipo_midia=art.kind.tipo,
                imagem=art.kind.imagem,
                tamanho=len(data),
                parte=i,
                partes=total,
                dados64=base64.b64encode(pedaco).decode("ascii"),
            )

    def do_cancel(self, cfg, entry, req, out, ip) -> None:
        job = self.jobs.cancel(_job_id(req), entry.name)
        if job is None:
            return out.error("não achei esse job", "job")
        self.audit_event("cancelar", ip, de=entry.name, job=job.id, comando=job.command)
        out.send("cancelado", fim=True, job=job.id, estado=job.state)

    def do_status(self, cfg, entry, req, out, ip) -> None:
        job = self.jobs.get(_job_id(req), entry.name)
        if job is None:
            return out.error("não achei esse job", "job")
        follow = req.get("seguir", False) is True
        # Metadados primeiro, depois a saída guardada em frames "saida" (um frame
        # só não caberia 2 x 256 KiB), e por fim o resultado.
        out.send("job", fim=False, job=job.id, estado=job.result_dict())
        self.stream(job, out, 0, follow=follow)

    def do_history(self, cfg, entry, req, out, ip) -> None:
        limit = req.get("limite", MAX_HISTORY)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_HISTORY:
            limit = MAX_HISTORY
        jobs = [j.summary() for j in self.jobs.recent(entry.name, limit)]
        out.send("historico", fim=True, jobs=jobs)


def _cleanup(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _job_id(req: dict) -> str | None:
    value = req.get("job")
    return value if isinstance(value, str) and 0 < len(value) <= JOB_ID_MAX else None


def _coalesce(chunks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for stream_name, text in chunks:
        if out and out[-1][0] == stream_name and len(out[-1][1]) + len(text) <= 32 * 1024:
            out[-1] = (stream_name, out[-1][1] + text)
        else:
            out.append((stream_name, text))
    return out


def _redact(specs, values: dict | None) -> dict | None:
    """Tira da auditoria parâmetros marcados como segredo e corta textos longos."""
    if values is None:
        return None
    secret = {s.nome for s in (specs or []) if s.segredo}
    out = {}
    for k, v in values.items():
        if k in secret:
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "…"
        else:
            out[k] = v
    return out
