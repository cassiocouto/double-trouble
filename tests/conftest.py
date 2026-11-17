"""Fixtures comuns. Nenhum teste usa o ~/.config/relay real."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Toda pasta do relay aponta pra dentro do tmp_path do teste."""
    base = tmp_path / "relay"
    monkeypatch.setenv("RELAY_DIR", str(base))
    monkeypatch.setenv("RELAY_STATE_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("RELAY_DATA_DIR", str(tmp_path / "dados"))
    # Nenhum teste fala com o Tailscale de verdade (quem precisa usa um falso).
    monkeypatch.setenv("RELAY_TAILSCALE", str(tmp_path / "sem-tailscale"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Comandos rodam num processo filho e importam relay.command, como no venv instalado.
    src = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", src)
    # Sem sessao grafica: nenhum teste deve conseguir mexer na tela.
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.delenv(key, raising=False)
    yield


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in ("Z", "X")


def wait_until(pred, timeout: float = 5.0, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


@pytest.fixture
def base(tmp_path) -> Path:
    """RELAY_DIR com a pasta de comandos, os dois com modo 700."""
    b = Path(os.environ["RELAY_DIR"])
    b.mkdir(mode=0o700, exist_ok=True)
    (b / "comandos").mkdir(mode=0o700, exist_ok=True)
    return b


@pytest.fixture
def make_cmd(base):
    def make(name: str, code: str, mode: int = 0o600) -> Path:
        p = base / "comandos" / f"{name}.py"
        p.write_text(code, encoding="utf-8")
        os.chmod(p, mode)
        return p

    return make


@pytest.fixture
def agent(tmp_path):
    """Fábrica de agentes v2 em threads, cada um com pasta própria. Controlador padrão: 'controle'."""
    from .helpers import AgentHarness

    harnesses = []

    def make(**opts):
        h = AgentHarness(tmp_path / f"agente{len(harnesses)}", **opts)
        harnesses.append(h)
        return h

    yield make
    for h in harnesses:
        h.stop()
