"""Ambiente da sessão gráfica, lido do systemd do usuário na hora de cada execução.

O agente pode ter subido antes do login gráfico, então o ambiente dele pode
não ter DISPLAY e afins. Perguntar ao systemd na hora resolve isso.
"""

from __future__ import annotations

import subprocess

SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
)


def parse_show_environment(text: str) -> dict[str, str]:
    env = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        # Valores com escape ($'...') são raros aqui e não vale a pena decodificar.
        if sep and key in SESSION_ENV_KEYS and not value.startswith("$'"):
            env[key] = value
    return env


def session_env() -> dict[str, str]:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if r.returncode != 0:
        return {}
    return parse_show_environment(r.stdout)
