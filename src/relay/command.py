"""Ajuda pequena pra quem escreve comandos do Relay.

    from relay.command import caller, detach, params

    p = params()               # {"texto": "oi", ...}, já validado pelo agente
    print(f"oi, {caller()}")   # nome da máquina que pediu
    detach(["xdg-open", url])  # abre um programa que continua depois do comando

Os parâmetros chegam como um objeto JSON no stdin. Eles não aparecem no `ps`
de outros usuários (ao contrário de argumentos de linha de comando).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from typing import Any

_cache: dict[str, Any] | None = None

SESSION_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")


def params() -> dict[str, Any]:
    """Os parâmetros que o agente validou. Vazio se o comando não tem PARAMETROS."""
    global _cache
    if _cache is None:
        if sys.stdin is None or sys.stdin.isatty():
            _cache = {}
        else:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
            _cache = data if isinstance(data, dict) else {}
    return dict(_cache)


def caller() -> str:
    """Nome da máquina que pediu a execução."""
    return os.environ.get("RELAY_CALLER", "")


def detach(argv: list[str]) -> None:
    """Inicia um programa que continua vivo depois que o comando acabar.

    O agente mata o grupo de processos do comando quando ele termina. Pra um
    navegador ou outro aplicativo continuar aberto, ele precisa sair desse
    grupo de propósito. Com o systemd do usuário disponível, o programa vira
    uma unidade própria (sai até do cgroup do agente). Senão, vira uma sessão
    nova com stdin, stdout e stderr desligados.
    """
    if not argv:
        raise ValueError("detach() precisa de um comando")
    runner = shutil.which("systemd-run")
    if runner:
        cmd = [runner, "--user", "--collect", "--quiet", f"--unit=relay-detached-{secrets.token_hex(4)}"]
        cmd += [f"--setenv={k}={os.environ[k]}" for k in SESSION_KEYS if k in os.environ]
        cmd += ["--", *argv]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
            if r.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
