"""O agente como serviço: espera o Tailscale, escuta, avisa o systemd e se reergue.

Ciclo
  1. Lê o config. Se estiver quebrado, espera e tenta de novo (sem sair).
  2. Descobre o IP (o do Tailscale, se escutar = "tailscale"). Se o Tailscale
     ainda não subiu, espera com intervalo crescente até 30 s. Nada de sair
     com erro e entrar em loop de reinício do systemd.
  3. Escuta e manda READY=1. Se o IP do Tailscale mudar, fecha e escuta no novo.
  4. Manda WATCHDOG=1 enquanto a thread do servidor estiver viva.
  5. No SIGTERM manda STOPPING=1, cancela os jobs e espera os grupos morrerem.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..core import tailscale
from ..core.config import Config, ConfigSource
from ..core.errors import PonteError
from . import systemd
from .jobs import Job, JobManager
from .server import AgentServer, log

READY_GRACE = 30.0  # depois disso manda READY mesmo sem Tailscale, pro systemctl não travar
CHECK_ADDRESS_EVERY = 30.0
MAX_BACKOFF = 30.0


class AgentService:
    def __init__(
        self,
        source: ConfigSource,
        audit: Callable[[dict], None] | None = None,
        base_dir=None,
        notify: Callable[..., bool] = systemd.notify,
        find_ip: Callable[[], str | None] | None = None,
        check_every: float = CHECK_ADDRESS_EVERY,
        max_backoff: float = MAX_BACKOFF,
        ready_grace: float = READY_GRACE,
    ):
        self.source = source
        self.audit = audit
        self.base_dir = base_dir
        self.notify = notify
        self.find_ip = find_ip or (lambda: tailscale.own_ip(4))
        self.check_every = check_every
        self.max_backoff = max_backoff
        self.ready_grace = ready_grace
        self.stop_event = threading.Event()
        cfg = source.get()
        self.jobs = JobManager(max_parallel=cfg.agent.max_parallel if cfg else 4, on_finish=self._job_done)
        self.server: AgentServer | None = None
        self.address: tuple[str, int] | None = None
        self._ready_sent = False
        self._status = ""

    # -- ajudantes --------------------------------------------------------
    def _set_status(self, text: str) -> None:
        if text != self._status:
            self._status = text
            log(text)
            self.notify(f"STATUS={text}")

    def _ready(self) -> None:
        if not self._ready_sent:
            self._ready_sent = True
            self.notify("READY=1", f"STATUS={self._status}")

    def _job_done(self, job: Job) -> None:
        if self.audit:
            r = job.result
            self.audit(
                {
                    "evento": "fim",
                    "job": job.id,
                    "de": job.controller,
                    "comando": job.command,
                    "parametros": job.params,
                    "args": [a[:200] for a in job.args],
                    "estado": job.state,
                    "codigo": r.codigo if r else None,
                    "duracao": job.duration(),
                    "truncado": bool(r and r.truncado),
                }
            )

    def host_for(self, cfg: Config) -> str | None:
        return self.find_ip() if cfg.agent.listen == "tailscale" else cfg.agent.listen

    def _wait(self, seconds: float) -> bool:
        """Espera, mandando watchdog. True se pediram pra parar."""
        interval = systemd.watchdog_interval()
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return self.stop_event.is_set()
            step = min(left, interval or left, 1.0)
            if self.stop_event.wait(step):
                return True
            if interval:
                self.notify("WATCHDOG=1")

    # -- ciclo ------------------------------------------------------------
    def run(self) -> None:
        started = time.monotonic()
        backoff = 1.0
        while not self.stop_event.is_set():
            cfg = self.source.get()
            if cfg is None:
                self._set_status(f"config com problema: {self.source.error}. Esperando conserto")
                if time.monotonic() - started > self.ready_grace:
                    self._ready()
                if self._wait(5):
                    break
                continue
            self.jobs.max_parallel = cfg.agent.max_parallel
            host = self.host_for(cfg)
            if not host:
                self._set_status("esperando o Tailscale subir (tailscale ip -4 ainda não respondeu)")
                if time.monotonic() - started > self.ready_grace:
                    self._ready()
                if self._wait(backoff):
                    break
                backoff = min(backoff * 2, self.max_backoff)
                continue
            try:
                srv = AgentServer(self.source, host, cfg.agent.port, self.jobs, self.audit, base_dir=self.base_dir)
            except OSError as e:
                self._set_status(
                    f"não consegui escutar em {host}:{cfg.agent.port} ({e.strerror or e}), tentando de novo"
                )
                if self._wait(backoff):
                    break
                backoff = min(backoff * 2, self.max_backoff)
                continue
            backoff = 1.0
            self.server = srv
            self.address = srv.server_address[:2]
            self._set_status(f"{cfg.name} escutando em {host}:{srv.server_address[1]}")
            self._ready()
            self._serve(srv, cfg)
            self.server = None
        self.notify("STOPPING=1")
        self._set_status("desligando, encerrando os jobs")
        self.jobs.shutdown()

    def _serve(self, srv: AgentServer, cfg: Config) -> None:
        thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()
        interval = systemd.watchdog_interval()
        last_check = time.monotonic()
        host = self.address[0] if self.address else None
        try:
            while not self.stop_event.is_set():
                if self.stop_event.wait(min(1.0, interval or 1.0)):
                    break
                if not thread.is_alive():
                    log("a thread do servidor morreu, reabrindo")
                    break
                if interval:
                    self.notify("WATCHDOG=1")
                now = time.monotonic()
                if now - last_check >= self.check_every:
                    last_check = now
                    current = self.source.get()
                    if current is None:
                        continue
                    if current.agent.port != cfg.agent.port or current.agent.listen != cfg.agent.listen:
                        log("endereço ou porta mudou no config, reabrindo")
                        break
                    if current.agent.listen == "tailscale":
                        ip = self.find_ip()
                        if ip and ip != host:
                            log(f"o IP do Tailscale mudou de {host} pra {ip}, reabrindo")
                            break
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(5)

    def stop(self) -> None:
        self.stop_event.set()


def check_not_root() -> None:
    import os

    if os.geteuid() == 0:
        raise PonteError("o agente não roda como root. Use: systemctl --user start ponte-agent")
