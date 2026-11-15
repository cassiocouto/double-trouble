"""Abre o app de verdade numa tela falsa (Xvfb), se GTK 4 e libadwaita existirem.

Aqui na máquina de nuvem não tem GTK, então pula. Nas suas máquinas roda.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
TESTS = Path(__file__).resolve().parents[1]


def gtk_available() -> bool:
    code = "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Adw"
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(
    not gtk_available() or shutil.which("xvfb-run") is None, reason="precisa de GTK 4, libadwaita e xvfb-run"
)

SCRIPT = r'''
import os, sys, threading, time
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
from pathlib import Path
from helpers import AgentHarness
from ponte.core import config as config_mod
root = Path(sys.argv[3])
h = AgentHarness(root / "agente")
h.make_cmd("oi", '"""Diz oi."""\nCATEGORIA = "Teste"\nprint("oi da outra")\n')
cfg, _ = h.controller_config()
cfg.name = "controle"
os.makedirs(os.environ["PONTE_DIR"], mode=0o700, exist_ok=True)
config_mod.save(cfg, Path(os.environ["PONTE_DIR"]) / "ponte.json")

from gi.repository import GLib
from ponte.gui import app as app_mod
from ponte.gui import window as window_mod

result = {"ok": False}
def drive(win):
    def step():
        m = win.machines.machines.get("agente")
        if m is None or not m.online:
            return True
        vm = win.show_commands("agente")
        def wait_cmds():
            if vm.loading:
                return True
            win.show_run("agente", vm.find("oi"))
            run = win._pages[-1].vm
            run.request_run({})
            def wait_run():
                if run.state == "rodando":
                    return True
                result["ok"] = run.output_text() == "oi da outra\n"
                win.show_history(); win.show_preferences()
                GLib.timeout_add(300, lambda: win.app.quit())
                return False
            GLib.timeout_add(100, wait_run)
            return False
        GLib.timeout_add(100, wait_cmds)
        return False
    GLib.timeout_add(200, step)

orig = window_mod.MainWindow.__init__
def init(self, app):
    orig(self, app)
    drive(self)
window_mod.MainWindow.__init__ = init
GLib.timeout_add_seconds(30, lambda: sys.exit(3))
app_mod.main(["ponte-gui"])
h.stop()
sys.exit(0 if result["ok"] else 2)
'''


def test_app_opens_runs_and_quits(tmp_path):
    env = {
        **os.environ,
        "PONTE_DIR": str(tmp_path / "ctl"),
        "PONTE_DATA_DIR": str(tmp_path / "dados"),
        "PONTE_STATE_DIR": str(tmp_path / "estado"),
        "PYTHONPATH": str(SRC),
        "GSK_RENDERER": "cairo",
    }
    r = subprocess.run(
        ["xvfb-run", "-a", sys.executable, "-c", SCRIPT, str(SRC), str(TESTS), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
