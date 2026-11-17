"""Tira um print da tela desta máquina e manda a imagem pra quem pediu.

Não mostra notificação, não pisca a tela e não usa atalho de teclado. No
GNOME (X11 ou Wayland) usa a API de screenshot do próprio shell, que é
silenciosa. Em outros ambientes tenta ferramentas de linha de comando.
"""

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from relay.command import params

ICONE = "applets-screenshooter-symbolic"
CATEGORIA = "Tela"
TIMEOUT = 20
PARAMETROS = [
    {"nome": "cursor", "tipo": "booleano", "rotulo": "Incluir o cursor do mouse"},
]

out_file = os.environ.get("RELAY_OUTPUT_FILE")
if not out_file:
    sys.exit("este comando precisa do Relay (RELAY_OUTPUT_FILE não está definido)")
cursor = params().get("cursor", False)
shot = Path(out_file).with_name("shot.png")


def gnome_shell() -> list[str]:
    # org.gnome.Shell.Screenshot(include_cursor, flash, filename). flash=false
    # não pisca a tela; o shell escreve o PNG direto, sem notificação.
    return [
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.gnome.Shell",
        "--object-path",
        "/org/gnome/Shell/Screenshot",
        "--method",
        "org.gnome.Shell.Screenshot.Screenshot",
        "true" if cursor else "false",
        "false",
        str(shot),
    ]


# Cada método escreve em shot.png. Ordem: primeiro os silenciosos do GNOME.
METHODS = [
    ("gnome-shell", gnome_shell()),
    ("grim", ["grim", str(shot)]),
    ("gnome-screenshot", ["gnome-screenshot"] + (["-p"] if cursor else []) + ["-f", str(shot)]),
    ("spectacle", ["spectacle", "-b", "-n", "-o", str(shot)]),
    ("scrot", ["scrot", "-o"] + (["-p"] if cursor else []) + [str(shot)]),
    ("maim", ["maim"] + (["--hidecursor"] if not cursor else []) + [str(shot)]),
    ("imagemagick", ["import", "-window", "root", str(shot)]),
]


def png_size(path: Path) -> str:
    """Largura x altura lendo o cabeçalho do PNG (IHDR), só com a stdlib."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            w, h = struct.unpack(">II", head[16:24])
            return f"{w}x{h}"
    except (OSError, struct.error):
        pass
    return "tamanho desconhecido"


def worked() -> bool:
    return shot.exists() and shot.stat().st_size > 0


errors = []
for name, cmd in METHODS:
    if shutil.which(cmd[0]) is None:
        continue
    shot.unlink(missing_ok=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{name}: {e}")
        continue
    if r.returncode == 0 and worked():
        os.replace(shot, out_file)
        print(f"print tirado com {name} ({png_size(Path(out_file))})")
        sys.exit(0)
    detail = (r.stderr or r.stdout or "sem detalhe").strip().splitlines()
    errors.append(f"{name}: {detail[-1] if detail else 'falhou'}")

print("não consegui tirar o print. Tentei:", file=sys.stderr)
for e in errors:
    print(f"  {e}", file=sys.stderr)
if not errors:
    print("  nenhuma ferramenta de screenshot encontrada", file=sys.stderr)
    print("  no GNOME, instale com: sudo apt install gnome-screenshot", file=sys.stderr)
sys.exit(1)
