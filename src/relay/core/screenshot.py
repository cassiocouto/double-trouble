"""Qual ferramenta usar pra tirar o print, escolhida nas Configurações.

O print é tirado na outra máquina (o comando 'screenshot'), mas quem escolhe a
ferramenta é o controlador, aqui. A escolha vai como parâmetro 'ferramenta' pro
comando. 'automático' deixa o comando tentar na ordem esperta (as silenciosas
primeiro). A máquina que tira o print precisa ter a ferramenta escolhida
instalada; senão o comando avisa.

Em tempo de execução, só a biblioteca padrão. A escolha fica num arquivo nesta
máquina (screenshot.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import ConfigError
from .safety import read_private_text, write_private
from .strictjson import loads_strict

AUTO = "automático"

# A lista que aparece nas Configurações. Tem que bater, na mesma ordem, com as
# 'opcoes' do parâmetro 'ferramenta' do comando screenshot (um teste confere).
TOOLS = [
    AUTO,
    "scrot",
    "maim",
    "imagemagick",
    "ffmpeg",
    "xfce4-screenshooter",
    "spectacle",
    "grim",
    "gnome-screenshot",
    "gnome-shell",
]


@dataclass
class ScreenshotSettings:
    tool: str = AUTO


def settings_path() -> Path:
    return paths.base_dir() / "screenshot.json"


def load_settings(path: Path | None = None) -> ScreenshotSettings:
    path = path or settings_path()
    if not path.exists():
        return ScreenshotSettings()
    data = loads_strict(read_private_text(path, "o config da captura de tela"))
    if not isinstance(data, dict):
        raise ConfigError("o config da captura de tela precisa ser um objeto JSON")
    tool = data.get("tool", AUTO)
    if not isinstance(tool, str) or tool not in TOOLS:
        tool = AUTO  # valor estranho ou ferramenta que não existe mais: volta pro automático
    return ScreenshotSettings(tool=tool)


def save_settings(s: ScreenshotSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    paths.ensure_private_dir(path.parent)
    write_private(path, json.dumps({"tool": s.tool}) + "\n", mode=0o600)
