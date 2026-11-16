"""Trava a tela desta máquina."""

import subprocess
import sys

ICONE = "system-lock-screen-symbolic"
CATEGORIA = "Tela"
CONFIRMAR = True
TIMEOUT = 20

attempts = [
    [
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.gnome.ScreenSaver",
        "--object-path",
        "/org/gnome/ScreenSaver",
        "--method",
        "org.gnome.ScreenSaver.Lock",
    ],
    ["loginctl", "lock-sessions"],
    ["xdg-screensaver", "lock"],
]
for cmd in attempts:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        continue
    if r.returncode == 0:
        print(f"tela travada (via {cmd[0]})")
        sys.exit(0)
print("não consegui travar a tela", file=sys.stderr)
sys.exit(1)
