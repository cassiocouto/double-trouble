"""Tira um print da tela desta máquina e manda a imagem pra quem pediu.

Não mostra notificação, não pisca a tela (nas silenciosas) e não usa atalho.

A ferramenta pode vir escolhida no parâmetro 'ferramenta'. Com 'automático' (o
padrão), o comando tenta na ordem esperta: as que pegam a tela em silêncio
primeiro, e as que piscam por último. Escolhendo uma na mão, ele usa só ela e
avisa claro se não estiver instalada.

Depende da sessão gráfica:
  * X11: scrot, maim, ImageMagick (import), ffmpeg, xfce4-screenshooter e
    spectacle pegam o print sem piscar. O gnome-screenshot e o gnome-shell
    também funcionam, mas piscam a tela ao salvar.
    Instale uma silenciosa: sudo apt install scrot
  * Wayland (GNOME): por segurança, o sistema bloqueia print silencioso de
    programas de fora (a API do shell responde "Screenshot is not allowed").
    Pra print silencioso, use a sessão "Ubuntu on Xorg" na tela de login.
    No KDE em Wayland, o spectacle funciona; em wlroots (sway), o grim.
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
# Tem que bater com relay.core.screenshot.TOOLS (um teste confere). O primeiro,
# 'automático', deixa o comando escolher sozinho.
PARAMETROS = [
    {"nome": "cursor", "tipo": "booleano", "rotulo": "Incluir o cursor do mouse"},
    {
        "nome": "ferramenta",
        "tipo": "escolha",
        "rotulo": "Ferramenta",
        "ajuda": "Qual programa tira o print. 'automático' tenta na ordem esperta.",
        "opcoes": [
            "automático",
            "scrot",
            "maim",
            "imagemagick",
            "ffmpeg",
            "xfce4-screenshooter",
            "spectacle",
            "grim",
            "gnome-screenshot",
            "gnome-shell",
        ],
        "padrao": "automático",
    },
]

AUTO = "automático"

out_file = os.environ.get("RELAY_OUTPUT_FILE")
if not out_file:
    sys.exit("este comando precisa do Relay (RELAY_OUTPUT_FILE não está definido)")
p = params()
cursor = p.get("cursor", False)
escolha = p.get("ferramenta", AUTO)
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


# Catálogo: nome -> comando que escreve em shot.png. O cursor entra onde a
# ferramenta sabe incluir; nas outras ele é ignorado.
CATALOG = {
    "scrot": ["scrot", "-o", *(["-p"] if cursor else []), str(shot)],
    "maim": ["maim", *([] if cursor else ["--hidecursor"]), str(shot)],
    "imagemagick": ["import", "-window", "root", str(shot)],
    "ffmpeg": [
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
    "xfce4-screenshooter": ["xfce4-screenshooter", "-f", "-s", str(shot)],
    "spectacle": ["spectacle", "-b", "-n", "-o", str(shot)],
    "grim": ["grim", *(["-c"] if cursor else []), str(shot)],
    "gnome-screenshot": ["gnome-screenshot", *(["-p"] if cursor else []), "-f", str(shot)],
    "gnome-shell": gnome_shell(),
}

# Ordem do 'automático'. No X11, as silenciosas primeiro; as que piscam
# (gnome-shell, gnome-screenshot) ficam como último recurso.
X11_SILENT = ["scrot", "maim", "imagemagick", "ffmpeg", "xfce4-screenshooter", "spectacle"]
X11_FLASH = ["gnome-shell", "gnome-screenshot"]
WAYLAND_ORDER = ["gnome-shell", "grim", "spectacle"]

FLASHES = {"gnome-screenshot", "gnome-shell"}  # podem piscar; as outras não piscam


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


if escolha == AUTO:
    order = WAYLAND_ORDER if wayland else [*X11_SILENT, *X11_FLASH]
elif escolha in CATALOG:
    order = [escolha]
else:
    sys.exit(f"não conheço a ferramenta '{escolha}'")

# Escolha na mão que não está instalada: avisa claro, sem cair pra outra.
if escolha != AUTO and shutil.which(CATALOG[escolha][0]) is None:
    sys.exit(f"o '{escolha}' não está instalado nesta máquina (precisa estar no PATH)")

errors = []
tried_any = False
for name in order:
    cmd = CATALOG[name]
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

if escolha != AUTO:
    print(f"não consegui tirar o print com o {escolha}.", file=sys.stderr)
else:
    print(f"não consegui tirar o print (sessão: {session or 'desconhecida'}).", file=sys.stderr)
if errors:
    print("Tentei:", file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)
if wayland and escolha == AUTO:
    print(
        "\nNo GNOME em Wayland o print silencioso é bloqueado por segurança.\n"
        "Pra um print sem nenhum aviso, na tela de login escolha 'Ubuntu on Xorg'\n"
        "(engrenagem no canto) e entre de novo. Aí instale: sudo apt install gnome-screenshot",
        file=sys.stderr,
    )
elif not tried_any:
    print("\nNenhuma ferramenta encontrada. Instale: sudo apt install gnome-screenshot", file=sys.stderr)
sys.exit(1)
