"""Monta a execução de um comando já resolvido e validado."""

from __future__ import annotations

import os
import sys
import threading

from ..core.commands import ResolvedCommand
from .runner import OutputCallback, RunResult, RunSpec, run_process
from .session_env import session_env

# Variáveis que nunca passam do agente pro comando.
STRIP_ENV = ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID", "LISTEN_FDS", "LISTEN_PID", "LISTEN_FDNAMES")


def build_spec(
    resolved: ResolvedCommand,
    args: list[str],
    caller: str,
    timeout: float,
    stdin_data: bytes | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunSpec:
    """Roda o arquivo pelo fd que foi conferido, nunca pelo caminho.

    /proc/self/fd/N aponta pro mesmo arquivo que passou nas checagens, mesmo
    se alguém trocar o arquivo no disco entre a checagem e a execução.
    """
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(session_env())
    env["RELAY_CALLER"] = caller
    env["RELAY_COMMAND"] = resolved.meta.nome
    env["RELAY_COMMAND_FILE"] = str(resolved.opened.path)
    if extra_env:
        env.update(extra_env)
    fd = resolved.opened.fd
    return RunSpec(
        argv=[sys.executable, f"/proc/self/fd/{fd}", *args],
        cwd=str(resolved.opened.path.parent),
        env=env,
        timeout=timeout,
        stdin_data=stdin_data,
        pass_fds=(fd,),
    )


def run_collect(spec: RunSpec, cancel: threading.Event | None = None) -> tuple[RunResult, str, str]:
    """Roda e junta a saída inteira, sem streaming."""
    out: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def collect(stream: str, text: str) -> None:
        out[stream].append(text)

    callback: OutputCallback = collect
    result = run_process(spec, callback, cancel)
    return result, "".join(out["stdout"]), "".join(out["stderr"])
