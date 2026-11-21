"""Qual ferramenta de print usar: ajuste salvo e em sincronia com o comando."""

from __future__ import annotations

from importlib import resources

from relay.core import commands, screenshot


def test_default_is_auto():
    assert screenshot.ScreenshotSettings().tool == screenshot.AUTO
    assert screenshot.TOOLS[0] == screenshot.AUTO


def test_save_and_load(tmp_path):
    p = tmp_path / "screenshot.json"
    screenshot.save_settings(screenshot.ScreenshotSettings(tool="scrot"), path=p)
    assert screenshot.load_settings(path=p).tool == "scrot"
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_absent_is_default(tmp_path):
    assert screenshot.load_settings(path=tmp_path / "nao-existe.json").tool == screenshot.AUTO


def test_unknown_tool_falls_back_to_auto(tmp_path):
    p = tmp_path / "screenshot.json"
    p.write_text('{"tool": "paint"}')
    p.chmod(0o600)
    assert screenshot.load_settings(path=p).tool == screenshot.AUTO


def test_command_options_match_tools():
    # as opções do parâmetro 'ferramenta' do comando têm que bater com TOOLS
    src = (resources.files("relay") / "data" / "exemplos" / "screenshot.py").read_bytes()
    meta = commands.parse_metadata("screenshot", src)
    fer = next(p for p in meta.parametros if p.nome == "ferramenta")
    assert fer.opcoes == screenshot.TOOLS
    assert fer.padrao == screenshot.AUTO
