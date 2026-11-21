"""Tira um print da tela desta máquina e manda a imagem pra quem pediu.

Não mostra notificação, não pisca a tela e não usa atalho de teclado.

Importante: o jeito que funciona depende da sessão gráfica.
  * X11: scrot, maim e ImageMagick tiram o print em silêncio, sem piscar a
    tela. O gnome-screenshot também funciona, mas pisca a tela ao salvar; se
    for o único instalado, o comando avisa pra instalar o scrot. Instale um:
    sudo apt install scrot
  * Wayland (GNOME): por segurança, o sistema bloqueia print silencioso de
    programas de fora (a API do shell responde "Screenshot is not allowed").
    Não há caminho sem algum aviso. Pra print silencioso, use a sessão "Ubuntu
    on Xorg" na tela de login, ou instale uma extensão que exponha isso.
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
session = os.environ.get("XDG_SESSION_TYPE", "").lower()
wayland = session == "wayland" or (not session and os.environ.get("WAYLAND_DISPLAY"))


def gnome_shell() -> list[str]:
    # Funciona no GNOME em X11 e no GNOME antigo. No GNOME novo em Wayland é
    # bloqueado ("Screenshot is not allowed"), então cai pros outros.
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


# (nome, comando). Cada um escreve em shot.png. As ferramentas que pegam a tela
# em silêncio (sem piscar) vêm primeiro; gnome-shell e gnome-screenshot ficam por
# último, porque piscam a tela ao salvar.
X11_SILENT = [
    ("scrot", ["scrot", "-o", *(["-p"] if cursor else []), str(shot)]),
    ("maim", ["maim", *([] if cursor else ["--hidecursor"]), str(shot)]),
    ("imagemagick", ["import", "-window", "root", str(shot)]),
    (
        "ffmpeg",
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-i",
            os.environ.get("DISPLAY", ":0"),
            "-frames:v",
            "1",
            str(shot),
        ],
    ),
]
X11_FLASH = [
    ("gnome-shell", gnome_shell()),  # só se nenhuma das silenciosas existir
    ("gnome-screenshot", ["gnome-screenshot", *(["-p"] if cursor else []), "-f", str(shot)]),
]
WAYLAND_METHODS = [
    ("gnome-shell", gnome_shell()),  # bloqueado no GNOME novo, mas tenta
    ("grim", ["grim", *(["-c"] if cursor else []), str(shot)]),  # só compositores wlroots
]

# No X11, as silenciosas primeiro; as que piscam (gnome-shell, gnome-screenshot)
# ficam como último recurso.
METHODS = WAYLAND_METHODS if wayland else [*X11_SILENT, *X11_FLASH]


FLASHES = {"gnome-screenshot", "gnome-shell"}  # podem piscar a tela; scrot/maim/import não piscam


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
tried_any = False
for name, cmd in METHODS:
    if shutil.which(cmd[0]) is None:
        continue
    tried_any = True
    shot.unlink(missing_ok=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{name}: {e}")
        continue
    if r.returncode == 0 and worked():
        os.replace(shot, out_file)
        print(f"print tirado com {name} ({png_size(Path(out_file))})")
        if name in FLASHES:
            print(f"obs: o {name} pisca a tela. Pra não piscar: sudo apt install scrot", file=sys.stderr)
        sys.exit(0)
    detail = (r.stderr or r.stdout or "sem detalhe").strip().splitlines()
    errors.append(f"{name}: {detail[-1] if detail else 'falhou'}")

print(f"não consegui tirar o print (sessão: {session or 'desconhecida'}).", file=sys.stderr)
if errors:
    print("Tentei:", file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)
if wayland:
    print(
        "\nNo GNOME em Wayland o print silencioso é bloqueado por segurança.\n"
        "Pra um print sem nenhum aviso, na tela de login escolha 'Ubuntu on Xorg'\n"
        "(engrenagem no canto) e entre de novo. Aí instale: sudo apt install gnome-screenshot",
        file=sys.stderr,
    )
elif not tried_any:
    print("\nNenhuma ferramenta encontrada. Instale: sudo apt install gnome-screenshot", file=sys.stderr)
sys.exit(1)
