"""install.sh e uninstall.sh de verdade, numa HOME falsa e com systemctl falso.

Sobe um venv novo e instala o pacote, então demora uns segundos.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# No Ubuntu 24.04 o /usr/bin/python3 é o 3.12. Na máquina de testes ele é outro, então
# prefere o 3.12 quando existir (é ele que tem o PyGObject do apt).
SYSTEM_PYTHON = "/usr/bin/python3.12" if os.access("/usr/bin/python3.12", os.X_OK) else "/usr/bin/python3"

pytestmark = pytest.mark.skipif(
    not os.access(SYSTEM_PYTHON, os.X_OK) or shutil.which("bash") is None, reason="precisa do python3 do sistema"
)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "systemctl.log"
    for name in ("systemctl",):
        f = bin_dir / name
        f.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$2" = "is-active" ] && exit 3\nexit 0\n')
        os.chmod(f, 0o700)
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RELAY_PYTHON": SYSTEM_PYTHON,
        "LANG": "C.UTF-8",
    }
    return home, env, log


def run(script, env, *args, stdin=None):
    return subprocess.run(
        ["bash", str(REPO / "scripts" / script), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_install_and_uninstall(home):
    home, env, log = home
    r = run("install.sh", env)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout

    venv = home / ".local" / "share" / "relay" / "venv"
    assert (venv / "bin" / "relay-agent").exists()
    for exe in ("relay", "relay-agent", "relay-gui"):
        assert os.readlink(home / ".local" / "bin" / exe) == str(venv / "bin" / exe)

    cfg_dir = home / ".config" / "relay"
    cfg = json.loads((cfg_dir / "relay.json").read_text())
    assert cfg["versao"] == 2
    mine = cfg_dir / "comandos" / "meu_comando.py"
    mine.write_text("print('meu')\n")
    os.chmod(mine, 0o600)

    # agente
    calls = log.read_text()
    unit = (home / ".config" / "systemd" / "user" / "relay-agent.service").read_text()
    assert f"ExecStart={venv}/bin/relay-agent" in unit
    assert "@VENV@" not in unit and "@REPO@" not in unit
    assert "--user enable relay-agent.service" in calls
    assert "--user restart --no-block relay-agent.service" in calls

    # interface: aqui não tem GTK 4, então ele mostra a linha do apt e pula a interface
    gtk_ok = (
        subprocess.run(
            [SYSTEM_PYTHON, "-c", "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')"],
            capture_output=True,
        ).returncode
        == 0
    )
    desktop = home / ".local" / "share" / "applications" / "dev.cass.Relay.desktop"
    if gtk_ok:
        assert desktop.exists()
    else:
        assert "sudo apt install" in out
        assert not desktop.exists()
    assert "sudo " not in calls  # nunca chamou sudo

    # o relay instalado funciona
    r2 = subprocess.run([str(venv / "bin" / "relay"), "comandos"], env=env, capture_output=True, text=True)
    assert "meu_comando" in r2.stdout

    # desinstalar tira o programa e deixa config e comandos
    r = run("uninstall.sh", env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not venv.exists()
    assert not (home / ".config" / "systemd" / "user" / "relay-agent.service").exists()
    assert not (home / ".local" / "bin" / "relay").exists()
    assert (cfg_dir / "relay.json").exists() and mine.exists()


def test_install_fresh_agent_only(home):
    home, env, _log = home
    r = run("install.sh", env, "--agente")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg_dir = home / ".config" / "relay"
    assert (cfg_dir / "relay.json").exists()
    assert oct(os.stat(cfg_dir / "relay.json").st_mode & 0o777) == "0o600"
    assert {p.name for p in (cfg_dir / "comandos").iterdir()} == {
        "status.py",
        "notificar.py",
        "travar_tela.py",
        "abrir_url.py",
        "screenshot.py",
    }
    assert not (home / ".local" / "share" / "applications" / "dev.cass.Relay.desktop").exists()


def test_install_refuses_root(home):
    _, env, _ = home
    if os.geteuid() != 0:
        pytest.skip("só dá pra testar rodando como root")
    r = run("install.sh", env)
    assert r.returncode == 1 and "Não rode como root" in r.stderr


def test_uninstall_everything_asks(home):
    home, env, _ = home
    cfg_dir = home / ".config" / "relay"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "x").write_text("x")
    r = run("uninstall.sh", env, "--tudo")  # sem terminal e sem --sim: não apaga
    assert "nada foi apagado" in r.stdout
    assert (cfg_dir / "x").exists()
    r = run("uninstall.sh", env, "--tudo", "--sim")
    assert not cfg_dir.exists()
