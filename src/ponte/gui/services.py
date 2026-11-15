"""Serviço do controlador: toda a rede roda fora da thread principal.

Os resultados voltam pela função `dispatch`, que na interface é um
GLib.idle_add (roda na thread da tela) e nos testes é chamada direta. Assim
a janela nunca congela e nenhum widget é tocado fora da thread certa.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..core import config as config_mod
from ..core.client import Client
from ..core.errors import (
    AuthError,
    CommandError,
    ConfigError,
    ConnectionFailed,
    ParamError,
    PonteError,
    ProtocolError,
    RemoteError,
)

Dispatch = Callable[..., None]


def friendly_error(exc: BaseException, machine: str = "") -> str:
    """Mensagem clara em português pra mostrar na tela."""
    who = machine or "a outra máquina"
    if isinstance(exc, ConnectionFailed):
        return f"Não deu pra falar com {who}. {exc}"
    if isinstance(exc, AuthError):
        return f"{who} não aceitou esta máquina ({exc}). Confira o pareamento nas Configurações."
    if isinstance(exc, ProtocolError):
        return str(exc)[:1].upper() + str(exc)[1:]
    if isinstance(exc, RemoteError):
        return f"{who} recusou: {exc}"
    if isinstance(exc, (ConfigError, ParamError, CommandError)):
        return str(exc)[:1].upper() + str(exc)[1:]
    if isinstance(exc, PonteError):
        return str(exc)
    return f"Erro inesperado: {exc!r}"


@dataclass
class RunHandle:
    """Um job em andamento, visto pela interface."""

    machine: str
    command: str
    job: str | None = None
    client: Client | None = None
    finished: bool = False

    def abort(self) -> None:
        if self.client:
            self.client.abort()


class ControllerService:
    def __init__(
        self,
        dispatch: Dispatch,
        load_config: Callable[[], config_mod.Config] = config_mod.load,
        client_factory: Callable[..., Client] = Client,
        max_workers: int = 8,
    ):
        self.dispatch = dispatch
        self.load_config = load_config
        self.client_factory = client_factory
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ponte-rede")
        self._closed = False
        self._lock = threading.Lock()

    # -- base -------------------------------------------------------------
    def submit(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        machine: str = "",
    ) -> None:
        def run() -> None:
            try:
                result = work()
            except BaseException as e:
                if on_error:
                    self.dispatch(on_error, friendly_error(e, machine))
                return
            if on_done:
                self.dispatch(on_done, result)

        with self._lock:
            if self._closed:
                return
            self.pool.submit(run)

    def client(self, machine: str, **kw) -> Client:
        cfg = self.load_config()
        return self.client_factory(cfg, cfg.find_machine(machine), **kw)

    def machine_names(self) -> list[str]:
        return sorted(self.load_config().machines)

    # -- ações ------------------------------------------------------------
    def ping(self, machine: str, on_done, on_error) -> None:
        self.submit(lambda: self.client(machine, read_timeout=10).ping(), on_done, on_error, machine)

    def list_commands(self, machine: str, on_done, on_error) -> None:
        self.submit(lambda: self.client(machine, read_timeout=15).list_commands(), on_done, on_error, machine)

    def run(
        self,
        machine: str,
        command: str,
        params: dict | None,
        args: list[str] | None,
        on_event: Callable[[dict], None],
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None],
    ) -> RunHandle:
        handle = RunHandle(machine=machine, command=command)

        def forward(ev: dict) -> None:
            if ev.get("tipo") == "aceito":
                handle.job = ev.get("job")
            self.dispatch(on_event, ev)

        def work():
            handle.client = self.client(machine)
            try:
                return handle.client.run(command, params, args, forward)
            finally:
                handle.finished = True

        self.submit(work, on_done, on_error, machine)
        return handle

    def follow(self, machine: str, job: str, on_event, on_done, on_error) -> None:
        self.submit(
            lambda: self.client(machine).status(job, True, lambda ev: self.dispatch(on_event, ev)),
            on_done,
            on_error,
            machine,
        )

    def cancel(self, machine: str, job: str, on_done, on_error) -> None:
        self.submit(lambda: self.client(machine, read_timeout=15).cancel(job), on_done, on_error, machine)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
