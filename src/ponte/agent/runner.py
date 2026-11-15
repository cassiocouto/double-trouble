"""Roda um processo no seu próprio grupo e garante que o grupo morre (invariante 9).

Regras
  * O comando vira líder de uma sessão nova (setsid), então ele e tudo que
    ele criar ficam no mesmo grupo de processos.
  * Quando o líder termina, quem sobrou no grupo recebe SIGTERM e, depois de
    um tempo, SIGKILL. Nada do grupo sobrevive ao job.
  * No timeout ou no cancelamento acontece o mesmo, só que antes.
  * O líder só é colhido (wait) depois que o grupo foi morto. Enquanto ele é
    zumbi o número do grupo não pode ser reusado por outro processo, então o
    killpg nunca acerta um processo alheio.
  * A saída é lida por pipe e guardada até um limite. O excesso é lido e
    jogado fora, pra o comando nunca travar por pipe cheio.

Quem quer deixar um programa rodando depois do comando (um navegador) precisa
soltar ele de propósito, com ponte.comando.soltar().
"""

from __future__ import annotations

import codecs
import os
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_OUTPUT_BYTES = 256 * 1024
KILL_GRACE = 3.0  # entre SIGTERM e SIGKILL
DRAIN_GRACE = 0.5  # quanto esperar EOF de quem fugiu do grupo e segura o pipe
POLL = 0.05

OutputCallback = Callable[[str, str], None]


@dataclass
class RunSpec:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    timeout: float
    stdin_data: bytes | None = None
    pass_fds: tuple[int, ...] = ()
    max_output: int = MAX_OUTPUT_BYTES


@dataclass
class RunResult:
    codigo: int | None
    estourou_tempo: bool = False
    cancelado: bool = False
    truncado: bool = False
    duracao: float = 0.0
    bytes_descartados: dict[str, int] = field(default_factory=dict)
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.codigo == 0 and not self.estourou_tempo and not self.cancelado and self.erro is None


def group_members(pgid: int, skip_zombies: bool = True) -> list[int]:
    """PIDs vivos no grupo de processos, lidos de /proc."""
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as f:
                data = f.read()
        except OSError:
            continue
        try:
            rest = data.rsplit(b")", 1)[1].split()
            state, pgrp = rest[0], int(rest[2])
        except (IndexError, ValueError):
            continue
        if pgrp != pgid:
            continue
        if skip_zombies and state in (b"Z", b"X"):
            continue
        found.append(int(entry))
    return found


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_exited(pid: int) -> bool:
    """True se o líder terminou, sem colher ele (WNOWAIT)."""
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    return info is not None


class _Reader:
    """Lê stdout e stderr sem bloquear, guarda até o limite e avisa quem ouve."""

    def __init__(self, proc: subprocess.Popen, on_output: OutputCallback, limit: int):
        self.on_output = on_output
        self.limit = limit
        self.sel = selectors.DefaultSelector()
        self.kept = {"stdout": 0, "stderr": 0}
        self.dropped = {"stdout": 0, "stderr": 0}
        self.decoders = {name: codecs.getincrementaldecoder("utf-8")(errors="replace") for name in ("stdout", "stderr")}
        self.files = {"stdout": proc.stdout, "stderr": proc.stderr}
        for name, f in self.files.items():
            assert f is not None
            os.set_blocking(f.fileno(), False)
            self.sel.register(f.fileno(), selectors.EVENT_READ, name)

    @property
    def open(self) -> bool:
        return bool(self.sel.get_map())

    def pump(self, timeout: float) -> None:
        if not self.open:
            time.sleep(timeout)
            return
        for key, _ in self.sel.select(timeout):
            name = key.data
            try:
                data = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                self._close(name)
                continue
            room = self.limit - self.kept[name]
            take = data[: max(room, 0)]
            self.dropped[name] += len(data) - len(take)
            if take:
                self.kept[name] += len(take)
                text = self.decoders[name].decode(take)
                if text:
                    self.on_output(name, text)

    def _close(self, name: str) -> None:
        f = self.files[name]
        if f is None or f.closed:
            return
        try:
            self.sel.unregister(f.fileno())
        except (KeyError, ValueError):
            pass
        f.close()

    def finish(self) -> None:
        for name in ("stdout", "stderr"):
            self._close(name)
            tail = self.decoders[name].decode(b"", final=True)
            if tail:
                self.on_output(name, tail)
        self.sel.close()


def run_process(
    spec: RunSpec,
    on_output: OutputCallback,
    cancel: threading.Event | None = None,
    on_start: Callable[[int], None] | None = None,
) -> RunResult:
    """Roda o processo até o fim, com timeout e cancelamento. Nunca deixa o grupo vivo."""
    cancel = cancel or threading.Event()
    start = time.monotonic()
    stdin_file = None
    if spec.stdin_data is not None:
        # Arquivo temporário (e não pipe) pra não travar se o comando não ler o stdin.
        stdin_file = tempfile.TemporaryFile()  # noqa: SIM115 (fechado no finally)
        stdin_file.write(spec.stdin_data)
        stdin_file.seek(0)
    try:
        proc = subprocess.Popen(
            spec.argv,
            cwd=spec.cwd,
            env=spec.env,
            stdin=stdin_file if stdin_file is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
            pass_fds=spec.pass_fds,
        )
    except OSError as e:
        return RunResult(codigo=None, erro=f"não consegui iniciar o comando ({e.strerror})")
    finally:
        if stdin_file is not None:
            stdin_file.close()

    pgid = proc.pid
    if on_start:
        on_start(proc.pid)
    reader = _Reader(proc, on_output, spec.max_output)
    deadline = start + spec.timeout
    reason: str | None = None
    term_at = 0.0
    killed = False
    try:
        while True:
            now = time.monotonic()
            if reason is None:
                if cancel.is_set():
                    reason = "cancel"
                elif now >= deadline:
                    reason = "timeout"
                elif _leader_exited(proc.pid):
                    reason = "exit"
                if reason is not None:
                    _killpg(pgid, signal.SIGTERM)
                    term_at = now
            if reason is not None:
                leader_done = _leader_exited(proc.pid)
                others = [p for p in group_members(pgid) if p != proc.pid]
                if leader_done and not others:
                    break
                if not killed and now - term_at >= KILL_GRACE:
                    _killpg(pgid, signal.SIGKILL)
                    killed = True
                if killed and now - term_at >= KILL_GRACE + 5:
                    break  # algo que não morre nem com SIGKILL (estado D); desiste de esperar
            reader.pump(POLL)
        # Grupo vazio. Agora sim colhe o líder e lê o que sobrou nos pipes.
        _killpg(pgid, signal.SIGKILL)
        proc.wait()
        drain_until = time.monotonic() + DRAIN_GRACE
        while reader.open and time.monotonic() < drain_until:
            reader.pump(POLL)
    finally:
        if proc.returncode is None:
            _killpg(pgid, signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        reader.finish()

    return RunResult(
        codigo=proc.returncode,
        estourou_tempo=reason == "timeout",
        cancelado=reason == "cancel",
        truncado=any(reader.dropped.values()),
        duracao=round(time.monotonic() - start, 3),
        bytes_descartados={k: v for k, v in reader.dropped.items() if v},
    )
