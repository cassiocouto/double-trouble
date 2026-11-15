#!/usr/bin/env bash
# Instala o ponte pro seu usuário, sem sudo.
#
#   scripts/install.sh            agente e interface (padrão)
#   scripts/install.sh --agente   só o agente (o lado que executa)
#   scripts/install.sh --gui      só a interface (o lado que manda)
#   scripts/install.sh --sim      não pergunta nada (aceita a migração do v1)
#
# O que ele faz
#   * venv em ~/.local/share/ponte/venv (com --system-site-packages, pra ver o PyGObject)
#   * links em ~/.local/bin (ponte, ponte-agent, ponte-gui)
#   * migra o ponte.py v1, se achar (pergunta antes)
#   * agente: ~/.config/systemd/user/ponte-agent.service, habilitado e ligado
#   * interface: ~/.local/share/applications/dev.cass.Ponte.desktop e o ícone
#
# Se faltar pacote do sistema, ele mostra a linha "sudo apt install ..." pra
# você rodar. Ele mesmo nunca roda sudo.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PONTE_PYTHON:-/usr/bin/python3}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA_HOME/ponte/venv"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
CONFIG_DIR="${PONTE_DIR:-$HOME/.config/ponte}"

want_agent=0
want_gui=0
assume_yes=0
recreate=0
for arg in "$@"; do
  case "$arg" in
    --agente) want_agent=1 ;;
    --gui) want_gui=1 ;;
    --sim) assume_yes=1 ;;
    --recriar-venv) recreate=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $arg (use --agente, --gui, --sim ou --recriar-venv)" >&2; exit 2 ;;
  esac
done
if [[ $want_agent == 0 && $want_gui == 0 ]]; then
  want_agent=1
  want_gui=1
fi

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
missing_apt=()
notes=()

if [[ "$(id -u)" == 0 ]]; then
  say "Não rode como root. O ponte é instalado e roda como o seu usuário." >&2
  exit 1
fi

# --------------------------------------------------------------------------
step "Conferindo o sistema"

if [[ ! -x "$PYTHON" ]]; then
  say "Não achei $PYTHON. Instale o Python do sistema:"
  say "  sudo apt install python3"
  exit 1
fi
py_version="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  say "O ponte precisa de Python 3.10 ou mais novo. Aqui tem o $py_version."
  exit 1
fi
say "Python $py_version em $PYTHON"

if ! "$PYTHON" -c 'import ensurepip, venv' 2>/dev/null; then
  missing_apt+=("python3-venv")
fi

gui_ok=1
if [[ $want_gui == 1 ]]; then
  if ! "$PYTHON" -c 'import gi' 2>/dev/null; then
    missing_apt+=("python3-gi")
    gui_ok=0
  fi
  if [[ $gui_ok == 1 ]] && ! "$PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' 2>/dev/null; then
    missing_apt+=("gir1.2-gtk-4.0")
    gui_ok=0
  fi
  if [[ $gui_ok == 1 ]] && ! "$PYTHON" -c 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw; assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)' 2>/dev/null; then
    missing_apt+=("gir1.2-adw-1")
    gui_ok=0
  fi
fi
if [[ $want_agent == 1 ]] && ! command -v notify-send >/dev/null 2>&1; then
  notes+=("O exemplo 'notificar' usa o notify-send. Pra ter ele: sudo apt install libnotify-bin")
fi
if ! command -v tailscale >/dev/null 2>&1; then
  notes+=("Não achei o comando tailscale. O agente escuta no IP do Tailscale, então instale e entre no seu tailnet.")
fi

if [[ ${#missing_apt[@]} -gt 0 ]]; then
  say ""
  say "Faltam pacotes do sistema. Rode esta linha e depois rode o instalador de novo:"
  say ""
  say "  sudo apt install ${missing_apt[*]}"
  say ""
  if printf '%s\n' "${missing_apt[@]}" | grep -q '^python3-venv$'; then
    exit 1
  fi
  if [[ $want_agent == 0 ]]; then
    exit 1
  fi
  say "Sigo instalando só o agente. A interface fica pra depois desses pacotes."
  want_gui=0
fi

# --------------------------------------------------------------------------
step "Venv em $VENV"

if [[ $recreate == 1 && -d "$VENV" ]]; then
  rm -rf "$VENV"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  mkdir -p "$(dirname "$VENV")"
  chmod 700 "$(dirname "$VENV")"
  "$PYTHON" -m venv --system-site-packages "$VENV"
fi
pip_args=(--quiet --disable-pip-version-check)
if "$VENV/bin/python" -c 'import setuptools, sys; sys.exit(0 if int(setuptools.__version__.split(".")[0]) >= 61 else 1)' 2>/dev/null; then
  # setuptools do sistema serve: instala sem precisar baixar nada
  pip_args+=(--no-build-isolation)
fi
# Monta o pacote numa cópia temporária, pra não sujar (nem precisar escrever em) o repositório.
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT
cp -r "$REPO/src" "$REPO/pyproject.toml" "$build_dir/"
[[ -f "$REPO/README.md" ]] && cp "$REPO/README.md" "$build_dir/"
"$VENV/bin/python" -m pip install "${pip_args[@]}" --upgrade "$build_dir"
say "ponte $("$VENV/bin/ponte" --versao | awk '{print $2}') instalado"

mkdir -p "$BIN_DIR"
for exe in ponte ponte-agent ponte-gui; do
  ln -sfn "$VENV/bin/$exe" "$BIN_DIR/$exe"
done
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) notes+=("$BIN_DIR não está no seu PATH. Saia e entre de novo na sessão, ou use $VENV/bin/ponte") ;;
esac

# --------------------------------------------------------------------------
step "Config em $CONFIG_DIR"

PONTE="$VENV/bin/ponte"
if [[ -f "$CONFIG_DIR/ponte.json" ]]; then
  say "já existe $CONFIG_DIR/ponte.json, não mexo"
elif [[ -f "$CONFIG_DIR/config.json" ]]; then
  say "Achei o config do ponte.py v1."
  if [[ $assume_yes == 1 ]]; then
    "$PONTE" migrar --sim
  elif [[ -t 0 ]]; then
    # Mostra o plano e pergunta antes de mudar qualquer coisa.
    "$PONTE" migrar || say "Pulei a migração. Rode depois:  ponte migrar"
  else
    say "Pulei a migração (sem terminal pra perguntar). Rode depois:  ponte migrar"
  fi
else
  "$PONTE" init
fi

# --------------------------------------------------------------------------
if [[ $want_agent == 1 ]]; then
  step "Agente (serviço de usuário)"
  mkdir -p "$UNIT_DIR"
  sed -e "s|@VENV@|$VENV|g" -e "s|@REPO@|$REPO|g" "$REPO/data/ponte-agent.service.in" > "$UNIT_DIR/ponte-agent.service"
  say "escrevi $UNIT_DIR/ponte-agent.service"
  if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user enable ponte-agent.service >/dev/null 2>&1 || true
    if [[ -f "$CONFIG_DIR/ponte.json" ]]; then
      systemctl --user restart --no-block ponte-agent.service
      say "agente ligado. Logs:  journalctl --user -u ponte-agent -f"
    else
      notes+=("O agente está habilitado mas não ligado, porque ainda não tem config. Depois: systemctl --user start ponte-agent")
    fi
  else
    notes+=("Não consegui falar com o systemd do usuário. Rode depois: systemctl --user daemon-reload && systemctl --user enable --now ponte-agent")
  fi

  port=47800
  pair_port=47801
  if [[ -f "$CONFIG_DIR/ponte.json" ]]; then
    read -r port pair_port < <("$VENV/bin/python" -c 'import json,sys; a=json.load(open(sys.argv[1]))["agente"]; print(a["porta"], a["porta_pareamento"])' "$CONFIG_DIR/ponte.json" 2>/dev/null || echo "47800 47801")
  fi
  ufw_on=0
  if [[ -r /etc/ufw/ufw.conf ]] && grep -qi '^ENABLED=yes' /etc/ufw/ufw.conf; then ufw_on=1; fi
  if systemctl is-active --quiet ufw 2>/dev/null && [[ $ufw_on == 1 ]]; then
    notes+=("O ufw está ativo. Pra liberar o ponte só na interface do Tailscale, rode:
    sudo ufw allow in on tailscale0 to any port $port proto tcp comment 'ponte'
    sudo ufw allow in on tailscale0 to any port $pair_port proto tcp comment 'ponte pareamento'")
  fi
fi

# --------------------------------------------------------------------------
if [[ $want_gui == 1 ]]; then
  step "Interface"
  mkdir -p "$APPS_DIR" "$ICON_DIR"
  sed -e "s|@VENV@|$VENV|g" "$REPO/data/dev.cass.Ponte.desktop.in" > "$APPS_DIR/dev.cass.Ponte.desktop"
  install -m 644 "$REPO/data/icons/dev.cass.Ponte.svg" "$ICON_DIR/dev.cass.Ponte.svg"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS_DIR" || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true
  say "Ponte aparece no menu de aplicativos (pode levar alguns segundos)"
fi

# --------------------------------------------------------------------------
step "Pronto"
if [[ ${#notes[@]} -gt 0 ]]; then
  for n in "${notes[@]}"; do
    say "* $n"
  done
  say ""
fi
say "Próximos passos"
say "  1. Na máquina que vai EXECUTAR:   ponte parear"
say "  2. Na máquina que vai MANDAR:     ponte parear --com <nome-da-outra>"
say "  3. Teste:                         ponte ping <nome-da-outra>"
