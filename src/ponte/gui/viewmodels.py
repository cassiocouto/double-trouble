"""View-models: o estado e as regras de cada tela, sem nenhum widget.

A tela liga nos sinais (Signal) e chama os métodos. Tudo aqui roda na thread
principal: o serviço já entrega os resultados da rede pelo dispatch.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core import config as config_mod
from ..core.errors import ConfigError, PonteError
from . import text
from .form import FormModel, remember_values
from .history import Entry, HistoryStore
from .services import ControllerService, RunHandle, friendly_error
from .signals import Signal

LONG_JOB = 10.0  # segundos; acima disso avisa por notificação se a janela não estiver em foco
SKEW_WARNING = 30.0


# ==========================================================================
# Máquinas


@dataclass
class MachineState:
    name: str
    address: str
    port: int
    status: str = "verificando"  # verificando, online, offline
    latency_ms: float | None = None
    skew: float | None = None
    version: str = ""
    running: int = 0
    error: str = ""

    @property
    def online(self) -> bool:
        return self.status == "online"

    @property
    def subtitle(self) -> str:
        if self.status == "verificando":
            return "verificando…"
        if self.status == "offline":
            return f"offline · {self.error}" if self.error else "offline"
        parts = ["online", f"{self.latency_ms:.0f} ms"]
        if self.skew is not None:
            parts.append(text.skew(self.skew))
        if self.version:
            parts.append(f"agente {self.version}")
        if self.running:
            parts.append(f"{self.running} job(s) rodando")
        return " · ".join(parts)

    @property
    def warning(self) -> str:
        if self.skew is not None and abs(self.skew) > SKEW_WARNING:
            return "Os relógios estão bem diferentes. Acima de 60 s os pedidos são recusados."
        return ""


class MachinesVM:
    def __init__(self, service: ControllerService):
        self.service = service
        self.machines: dict[str, MachineState] = {}
        self.this_machine = ""
        self.config_error = ""
        self.changed = Signal()
        self.machine_changed = Signal()

    def reload(self) -> None:
        try:
            cfg = self.service.load_config()
        except ConfigError as e:
            self.config_error = friendly_error(e)
            self.machines = {}
            self.changed.emit()
            return
        self.config_error = ""
        self.this_machine = cfg.name
        old = self.machines
        self.machines = {}
        for name in sorted(cfg.machines):
            m = cfg.machines[name]
            state = old.get(name) or MachineState(name=name, address=m.address, port=m.port)
            state.address, state.port = m.address, m.port
            self.machines[name] = state
        self.changed.emit()

    def refresh(self) -> None:
        for name in list(self.machines):
            self.ping(name)

    def ping(self, name: str) -> None:
        state = self.machines.get(name)
        if state is None:
            return
        if state.status != "online":
            state.status = "verificando"
        self.machine_changed.emit(name)

        def ok(pong) -> None:
            if name not in self.machines:
                return
            state.status = "online"
            state.latency_ms = pong.latency_ms
            state.skew = pong.clock_skew
            state.version = pong.version
            state.running = pong.running
            state.error = ""
            self.machine_changed.emit(name)

        def fail(msg: str) -> None:
            if name not in self.machines:
                return
            state.status = "offline"
            state.error = msg
            self.machine_changed.emit(name)

        self.service.ping(name, ok, fail)


# ==========================================================================
# Comandos de uma máquina


class CommandsVM:
    def __init__(self, service: ControllerService, machine: str):
        self.service = service
        self.machine = machine
        self.commands: list[dict] = []
        self.loading = False
        self.error = ""
        self.query = ""
        self.changed = Signal()

    def load(self) -> None:
        self.loading = True
        self.error = ""
        self.changed.emit()

        def ok(items: list[dict]) -> None:
            self.loading = False
            self.commands = sorted(items, key=lambda c: c["nome"])
            self.changed.emit()

        def fail(msg: str) -> None:
            self.loading = False
            self.error = msg
            self.changed.emit()

        self.service.list_commands(self.machine, ok, fail)

    def set_query(self, query: str) -> None:
        self.query = query
        self.changed.emit()

    def visible(self) -> list[dict]:
        q = text.fold(self.query.strip())
        if not q:
            return list(self.commands)
        out = []
        for c in self.commands:
            hay = text.fold(" ".join(str(c.get(k) or "") for k in ("nome", "descricao", "categoria", "detalhes")))
            if all(word in hay for word in q.split()):
                out.append(c)
        return out

    def groups(self) -> list[tuple[str, list[dict]]]:
        grouped: dict[str, list[dict]] = {}
        for c in self.visible():
            grouped.setdefault(str(c.get("categoria") or "Outros"), []).append(c)
        order = sorted(grouped, key=lambda k: (k == "Outros", text.fold(k)))
        return [(k, grouped[k]) for k in order]

    def find(self, name: str) -> dict | None:
        return next((c for c in self.commands if c["nome"] == name), None)


# ==========================================================================
# Executar


class RunVM:
    READY = "pronto"
    CONFIRMING = "confirmando"
    RUNNING = "rodando"
    DONE = "terminado"
    FAILED = "erro"

    def __init__(
        self,
        service: ControllerService,
        machine: str,
        command: dict,
        history: HistoryStore | None = None,
        notifier: Callable[[str, str], None] | None = None,
        is_focused: Callable[[], bool] = lambda: True,
        previous_values: dict | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.service = service
        self.machine = machine
        self.command = command
        self.form = FormModel(command)
        self.history = history
        self.notifier = notifier
        self.is_focused = is_focused
        self.clock = clock
        self.values = self.form.initial_values(previous_values)
        self.state = self.READY if self.form.runnable else self.FAILED
        self.message = ""
        if not self.form.runnable:
            self.message = self.command.get("bloqueado") or self.form.error or "Este comando não pode rodar"
        self.errors: dict[str, str] = {}
        self.output: list[tuple[str, str]] = []
        self.job: str | None = None
        self.result: dict | None = None
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.handle: RunHandle | None = None
        self.cancel_requested = False
        self._pending: tuple[dict | None, list[str] | None] | None = None
        self._row: int | None = None
        self.changed = Signal()
        self.output_added = Signal()  # (fluxo, texto)
        self.output_cleared = Signal()
        self.errors_changed = Signal()
        self.confirm_requested = Signal()  # (pergunta)

    # -- rodar ------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state in (self.RUNNING, self.CONFIRMING)

    def request_run(self, values: dict) -> None:
        if self.busy or not self.form.runnable:
            return
        self.values = dict(values)
        params, args, errors = self.form.collect(values)
        self.errors = errors
        self.errors_changed.emit()
        if errors:
            return
        self._pending = (params, args)
        if self.form.needs_confirmation:
            self.state = self.CONFIRMING
            self.changed.emit()
            self.confirm_requested.emit(f"Rodar “{self.command['nome']}” em {self.machine}?")
            return
        self._start()

    def confirm(self, yes: bool) -> None:
        if self.state != self.CONFIRMING:
            return
        if yes:
            self._start()
        else:
            self.state = self.READY
            self._pending = None
            self.changed.emit()

    def _start(self) -> None:
        assert self._pending is not None
        params, args = self._pending
        self._pending = None
        self.output = []
        self.output_cleared.emit()
        self.job = None
        self.result = None
        self.message = ""
        self.cancel_requested = False
        self.state = self.RUNNING
        self.started_at = self.clock()
        self.ended_at = None
        if self.history:
            self._row = self.history.start(self.machine, self.command["nome"], remember_values(self.form, params, args))
        self.changed.emit()
        self.handle = self.service.run(
            self.machine, self.command["nome"], params, args, self._on_event, self._on_done, self._on_error
        )

    def cancel(self) -> None:
        if self.state != self.RUNNING or self.cancel_requested:
            return
        self.cancel_requested = True
        self.message = "Cancelando…"
        self.changed.emit()
        job = self.job or (self.handle.job if self.handle else None)
        if not job:
            # Ainda não chegou o id do job. Corta a conexão; o agente mantém o timeout.
            if self.handle:
                self.handle.abort()
            return

        def fail(msg: str) -> None:
            self.cancel_requested = False
            self.message = f"Não consegui cancelar: {msg}"
            self.changed.emit()

        self.service.cancel(self.machine, job, lambda _r: None, fail)

    # -- eventos da rede ----------------------------------------------------
    def _on_event(self, ev: dict) -> None:
        tipo = ev.get("tipo")
        if tipo == "aceito":
            self.job = ev.get("job")
            self.changed.emit()
            if self.cancel_requested:
                self.cancel_requested = False
                self.cancel()
        elif tipo == "saida":
            stream = "stderr" if ev.get("fluxo") == "stderr" else "stdout"
            data = ev.get("dados")
            if isinstance(data, str) and data:
                self.output.append((stream, data))
                self.output_added.emit(stream, data)

    def _on_done(self, outcome) -> None:
        self.ended_at = self.clock()
        self.result = outcome.result if isinstance(outcome.result, dict) else None
        self.state = self.DONE
        self.message = self.result_text()
        self._finish_history(error=None)
        self.changed.emit()
        self._maybe_notify()

    def _on_error(self, msg: str) -> None:
        self.ended_at = self.clock()
        self.state = self.FAILED
        self.message = msg
        self._finish_history(error=msg)
        self.changed.emit()

    def _finish_history(self, error: str | None) -> None:
        if not self.history or self._row is None:
            return
        r = self.result or {}
        state = r.get("estado") or ("erro" if error else "desconhecido")
        self.history.finish(
            self._row,
            job=self.job,
            state=state,
            code=r.get("codigo") if isinstance(r.get("codigo"), int) else None,
            duration=r.get("duracao") if isinstance(r.get("duracao"), (int, float)) else self.elapsed(),
            stdout=self.stream_text("stdout"),
            stderr=self.stream_text("stderr"),
            truncated=bool(r.get("truncado")),
            error=error,
        )
        self._row = None

    def _maybe_notify(self) -> None:
        if not self.notifier or self.is_focused():
            return
        if self.elapsed() < LONG_JOB:
            return
        self.notifier(f"{self.command['nome']} terminou em {self.machine}", self.result_text())

    # -- textos -----------------------------------------------------------
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else self.clock()
        return max(0.0, end - self.started_at)

    def result_text(self) -> str:
        r = self.result
        if not r:
            return "Terminou sem resultado"
        estado = r.get("estado")
        dur = text.duration(r.get("duracao") if isinstance(r.get("duracao"), (int, float)) else self.elapsed())
        code = r.get("codigo")
        if estado == "ok":
            return f"Deu certo em {dur} (código 0)"
        if estado == "falhou":
            return f"Falhou com código {code} em {dur}"
        if estado == "cancelado":
            return f"Cancelado depois de {dur}"
        if estado == "tempo_esgotado":
            return f"Passou do limite de {text.duration(r.get('timeout'))} e foi encerrado"
        if estado == "erro":
            return f"Não iniciou: {r.get('erro') or 'sem detalhe'}"
        return text.state(estado)

    def status_text(self) -> str:
        if self.state == self.RUNNING:
            base = f"Rodando há {text.duration(self.elapsed())}"
            return f"{base} · {self.message}" if self.message else base
        return self.message

    def stream_text(self, stream: str) -> str:
        return "".join(t for s, t in self.output if s == stream)

    def output_text(self) -> str:
        return "".join(t for _, t in self.output)


# ==========================================================================
# Histórico


class HistoryVM:
    def __init__(self, store: HistoryStore):
        self.store = store
        self.entries: list[Entry] = []
        self.changed = Signal()

    def reload(self) -> None:
        self.entries = self.store.list()
        self.changed.emit()

    def clear(self) -> None:
        self.store.clear()
        self.reload()

    @staticmethod
    def title(e: Entry) -> str:
        return f"{e.command} em {e.machine}"

    @staticmethod
    def subtitle(e: Entry) -> str:
        parts = [text.when(e.started), text.state(e.state)]
        if e.code is not None and e.state not in ("ok",):
            parts.append(f"código {e.code}")
        if e.duration is not None:
            parts.append(text.duration(e.duration))
        return " · ".join(p for p in parts if p)


# ==========================================================================
# Configurações


class SettingsVM:
    def __init__(self, path=None):
        self.path = path
        self.changed = Signal()
        self.error = ""

    def load(self) -> config_mod.Config | None:
        try:
            cfg = config_mod.load(self.path)
            self.error = ""
            return cfg
        except ConfigError as e:
            self.error = friendly_error(e)
            return None

    def _update(self, change) -> str | None:
        try:
            config_mod.update(change, self.path)
        except PonteError as e:
            return friendly_error(e)
        self.changed.emit()
        return None

    def add_machine(self, name: str, address: str, port: int | float, secret_hex: str) -> str | None:
        try:
            clean = config_mod.check_machine_name(config_mod.normalize_machine_name(name))
            secret = config_mod.parse_secret(secret_hex, clean)
        except ConfigError as e:
            return friendly_error(e)
        address = address.strip() or clean

        def change(cfg: config_mod.Config) -> None:
            cfg.machines[clean] = config_mod.MachineEntry(clean, address, int(port), secret)

        return self._update(change)

    def edit_machine(self, name: str, address: str, port: int | float) -> str | None:
        def change(cfg: config_mod.Config) -> None:
            m = cfg.machines.get(name)
            if m is None:
                raise ConfigError(f"não conheço a máquina '{name}'")
            m.address = address.strip() or name
            m.port = int(port)

        return self._update(change)

    def remove_machine(self, name: str) -> str | None:
        return self._update(lambda cfg: cfg.machines.pop(name, None))

    def revoke_controller(self, name: str) -> str | None:
        return self._update(lambda cfg: cfg.agent.controllers.pop(name, None))

    def set_controller_commands(self, name: str, commands: list[str] | None) -> str | None:
        def change(cfg: config_mod.Config) -> None:
            entry = cfg.agent.controllers.get(name)
            if entry is None:
                raise ConfigError(f"'{name}' não está autorizada")
            entry.commands = commands

        return self._update(change)

    @staticmethod
    def controller_summary(entry: config_mod.ControllerEntry) -> str:
        if entry.commands is None:
            return "pode rodar todos os comandos"
        if not entry.commands:
            return "não pode rodar nenhum comando"
        return "só pode rodar: " + ", ".join(entry.commands)
