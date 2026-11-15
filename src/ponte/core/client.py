"""Cliente do protocolo v2, usado pela CLI e pela interface (lado do controlador)."""

from __future__ import annotations

import contextlib
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from . import protocol
from .config import Config, MachineEntry
from .errors import AuthError, ConnectionFailed, ProtocolError, RemoteError

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30  # o agente manda "vivo" a cada 10 s enquanto um job roda em silêncio


@dataclass
class Pong:
    machine: str
    latency_ms: float
    clock_skew: float
    version: str
    protocol: int
    running: int


@dataclass
class RunOutcome:
    job: str | None
    result: dict | None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)

    @property
    def text_stdout(self) -> str:
        return "".join(self.stdout)

    @property
    def text_stderr(self) -> str:
        return "".join(self.stderr)


EventCallback = Callable[[dict], None]


class Client:
    def __init__(
        self,
        cfg: Config,
        machine: MachineEntry,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
    ):
        self.cfg = cfg
        self.machine = machine
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.key = protocol.derive_key(machine.secret, cfg.name, machine.name)
        self._sock: socket.socket | None = None

    @classmethod
    def for_machine(cls, cfg: Config, query: str, **kw) -> Client:
        return cls(cfg, cfg.find_machine(query), **kw)

    # -- baixo nível ------------------------------------------------------
    def _connect(self) -> socket.socket:
        m = self.machine
        try:
            sock = socket.create_connection((m.address, m.port), timeout=self.connect_timeout)
        except socket.gaierror:
            raise ConnectionFailed(
                f"não achei o endereço '{m.address}'. O Tailscale está ligado? (tailscale status)"
            ) from None
        except TimeoutError:
            raise ConnectionFailed(f"{m.name} não respondeu em {self.connect_timeout:.0f} s") from None
        except OSError as e:
            raise ConnectionFailed(
                f"não consegui falar com {m.name} em {m.address}:{m.port} ({e.strerror or e})"
            ) from None
        sock.settimeout(self.read_timeout)
        try:
            sock.sendall(protocol.MAGIC)
            answer = protocol.read_magic(sock)
        except TimeoutError:
            sock.close()
            raise ConnectionFailed(f"{m.name} aceitou a conexão mas não respondeu") from None
        except OSError as e:
            sock.close()
            raise ConnectionFailed(f"a conexão com {m.name} caiu ({e})") from None
        if answer != protocol.MAGIC:
            sock.close()
            if answer.startswith(protocol.MAGIC_PREFIX):
                other = answer.decode("ascii", "replace").strip()
                raise ProtocolError(
                    f"{m.name} fala outra versão do protocolo ({other}, aqui é PONTE/{protocol.PROTOCOL}). "
                    "Atualize o ponte nas duas máquinas"
                )
            raise ProtocolError(
                f"{m.name} fechou a conexão sem responder. Pode ser o ponte.py antigo (v1) rodando lá, "
                "ou este IP fora da lista de permitidos dela"
            )
        return sock

    def request(self, action: str, **fields) -> Iterator[dict]:
        """Manda um pedido e devolve os frames de resposta, já conferidos."""
        req = protocol.build_request(self.key, self.cfg.name, self.machine.name, action, **fields)
        verifier = protocol.ResponseVerifier(
            key=self.key, controller=self.cfg.name, agent=self.machine.name, nonce=req["nonce"]
        )
        sock = self._connect()
        self._sock = sock
        try:
            try:
                protocol.send_frame(sock, req, protocol.MAX_REQUEST_FRAME)
            except OSError as e:
                raise ConnectionFailed(f"a conexão com {self.machine.name} caiu ({e})") from None
            while not verifier.done:
                try:
                    frame = protocol.recv_frame(sock, protocol.MAX_RESPONSE_FRAME)
                except TimeoutError:
                    raise ConnectionFailed(f"{self.machine.name} parou de responder") from None
                except OSError as e:
                    raise ConnectionFailed(f"a conexão com {self.machine.name} caiu ({e})") from None
                except ProtocolError as e:
                    if verifier.next_seq and "conexão fechada" in str(e):
                        raise ConnectionFailed(f"a conexão com {self.machine.name} caiu no meio da resposta") from None
                    raise
                frame = verifier.check(frame)
                if frame.get("tipo") == "erro":
                    raise RemoteError(_text(frame.get("erro")) or "erro sem detalhe", _text(frame.get("codigo_erro")))
                yield frame
        finally:
            self._sock = None
            with contextlib.suppress(OSError):
                sock.close()

    def abort(self) -> None:
        """Fecha a conexão em andamento (o job continua no agente)."""
        sock = self._sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    def call(self, action: str, **fields) -> dict:
        last = None
        for frame in self.request(action, **fields):
            last = frame
        if last is None:  # pragma: no cover - request sempre termina com fim ou exceção
            raise AuthError("resposta vazia")
        return last

    # -- ações ------------------------------------------------------------
    def ping(self) -> Pong:
        t0 = time.monotonic()
        sent = time.time()
        frame = self.call("ping")
        elapsed = time.monotonic() - t0
        hora = frame.get("hora")
        skew = 0.0
        if isinstance(hora, (int, float)) and not isinstance(hora, bool):
            skew = float(hora) - (sent + elapsed / 2)
        return Pong(
            machine=self.machine.name,
            latency_ms=round(elapsed * 1000, 1),
            clock_skew=round(skew, 2),
            version=str(frame.get("versao", "?"))[:20],
            protocol=frame.get("protocolo") if isinstance(frame.get("protocolo"), int) else 0,
            running=frame.get("jobs_rodando") if isinstance(frame.get("jobs_rodando"), int) else 0,
        )

    def list_commands(self) -> list[dict]:
        frame = self.call("listar")
        items = frame.get("comandos")
        if not isinstance(items, list):
            raise AuthError("lista de comandos inválida")
        return [c for c in items if isinstance(c, dict) and isinstance(c.get("nome"), str)]

    def run(
        self,
        command: str,
        params: dict | None = None,
        args: list[str] | None = None,
        on_event: EventCallback | None = None,
        follow: bool = True,
    ) -> RunOutcome:
        fields: dict = {"comando": command, "seguir": follow}
        if params is not None:
            fields["parametros"] = params
        if args:
            fields["args"] = list(args)
        return self._collect(self.request("rodar", **fields), on_event)

    def status(self, job: str, follow: bool = False, on_event: EventCallback | None = None) -> RunOutcome:
        return self._collect(self.request("status", job=job, seguir=follow), on_event)

    def cancel(self, job: str) -> dict:
        return self.call("cancelar", job=job)

    def history(self, limit: int = 50) -> list[dict]:
        frame = self.call("historico", limite=limit)
        jobs = frame.get("jobs")
        return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []

    @staticmethod
    def _collect(frames: Iterator[dict], on_event: EventCallback | None) -> RunOutcome:
        outcome = RunOutcome(job=None, result=None)
        for frame in frames:
            tipo = frame.get("tipo")
            if tipo in ("aceito", "job") and isinstance(frame.get("job"), str):
                outcome.job = frame["job"]
                if tipo == "job" and isinstance(frame.get("estado"), dict):
                    outcome.result = frame["estado"]
            elif tipo == "saida":
                data = frame.get("dados")
                if isinstance(data, str):
                    target = outcome.stderr if frame.get("fluxo") == "stderr" else outcome.stdout
                    target.append(data)
            elif tipo == "fim" and isinstance(frame.get("resultado"), dict):
                outcome.result = frame["resultado"]
            if on_event:
                on_event(frame)
        return outcome


def _text(value: object) -> str:
    return value[:500] if isinstance(value, str) else ""
