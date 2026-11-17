"""Monta a execução de um comando já resolvido e validado."""

from __future__ import annotations

import errno
import os
import stat
import sys
import threading
from dataclasses import dataclass

from ..core.commands import ResolvedCommand
from ..core.media import MAX_ARTIFACT_BYTES, MediaKind, sniff
from .runner import OutputCallback, RunResult, RunSpec, run_process
from .session_env import session_env

# Variáveis que nunca passam do agente pro comando.
STRIP_ENV = ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID", "LISTEN_FDS", "LISTEN_PID", "LISTEN_FDNAMES")


@dataclass
class Artifact:
    data: bytes
    kind: MediaKind


def read_artifact(path: str, max_bytes: int = MAX_ARTIFACT_BYTES) -> Artifact | None:
    """Lê o arquivo que o comando escreveu em RELAY_OUTPUT_FILE, com cuidado.

    Devolve None se o comando não escreveu nada. Não segue link simbólico e só
    aceita arquivo comum: um comando bugado não pode fazer o agente devolver,
    por exemplo, /etc/shadow por um link (o comando já roda como o usuário, mas
    melhor fechar a porta). Acima do limite, levanta ValueError.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError("o arquivo de saída é um link simbólico") from None
        raise ValueError(f"não consegui ler o arquivo de saída ({e.strerror or e})") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("o arquivo de saída não é um arquivo comum")
        if st.st_size == 0:
            return None
        if st.st_size > max_bytes:
            raise ValueError(f"o arquivo de saída tem {st.st_size} bytes (máximo {max_bytes})")
        data = f.read(max_bytes + 1)
    if not data:
        return None
    if len(data) > max_bytes:
        raise ValueError(f"o arquivo de saída passou de {max_bytes} bytes")
    return Artifact(data=data, kind=sniff(data))


def build_spec(
    resolved: ResolvedCommand,
    args: list[str],
    caller: str,
    timeout: float,
    stdin_data: bytes | None = None,
    extra_env: dict[str, str] | None = None,
    output_file: str | None = None,
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
    if output_file:
        # Onde o comando escreve uma imagem, PDF etc. que volta pro controlador.
        env["RELAY_OUTPUT_FILE"] = output_file
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
