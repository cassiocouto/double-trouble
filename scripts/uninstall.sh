#!/usr/bin/env bash
# Desinstala o ponte do seu usuário, sem sudo.
#
#   scripts/uninstall.sh          tira o serviço, o venv, os links, o atalho e o ícone
#   scripts/uninstall.sh --tudo   também apaga config, comandos, auditoria e histórico
#                                 (pergunta antes; com --sim não pergunta)
#
# Sem --tudo, ~/.config/ponte (config e comandos), ~/.local/state/ponte
# (auditoria) e o histórico da interface ficam onde estão.

set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA_HOME/ponte/venv"
CONFIG_DIR="${PONTE_DIR:-$HOME/.config/ponte}"
STATE_DIR="$HOME/.local/state/ponte"

everything=0
assume_yes=0
for arg in "$@"; do
  case "$arg" in
    --tudo) everything=1 ;;
    --sim) assume_yes=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }

if systemctl --user cat ponte-agent.service >/dev/null 2>&1; then
  systemctl --user disable --now ponte-agent.service >/dev/null 2>&1 || true
  say "agente parado e desabilitado"
fi
rm -f "$HOME/.config/systemd/user/ponte-agent.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

for exe in ponte ponte-agent ponte-gui; do
  link="$HOME/.local/bin/$exe"
  if [[ -L "$link" && "$(readlink "$link")" == "$VENV/bin/$exe" ]]; then
    rm -f "$link"
  fi
done
rm -f "$DATA_HOME/applications/dev.cass.Ponte.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/dev.cass.Ponte.svg"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$DATA_HOME/applications" || true
rm -rf "$VENV"
say "venv, links, atalho e ícone removidos"

if [[ $everything == 1 ]]; then
  say ""
  say "Isto apaga, sem volta:"
  say "  $CONFIG_DIR   (config, segredos e TODOS os seus comandos)"
  say "  $STATE_DIR    (auditoria)"
  say "  $DATA_HOME/ponte   (histórico da interface)"
  answer="n"
  if [[ $assume_yes == 1 ]]; then
    answer="s"
  elif [[ -t 0 ]]; then
    read -r -p "Apagar mesmo? [s/N] " answer || answer="n"
  fi
  if [[ "$answer" =~ ^([sS]|[sS][iI][mM])$ ]]; then
    rm -rf "$CONFIG_DIR" "$STATE_DIR" "$DATA_HOME/ponte"
    say "apagado"
  else
    say "nada foi apagado"
  fi
else
  say "config e comandos continuam em $CONFIG_DIR"
fi
say ""
say "Se você tinha o ponte.py v1 e quer voltar pra ele:"
say "  systemctl --user enable --now ponte.service"
