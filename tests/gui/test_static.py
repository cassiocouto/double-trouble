"""Checagens da interface que não precisam de tela nem de GTK instalado."""

from __future__ import annotations

import ast
import re
from pathlib import Path

GUI = Path(__file__).resolve().parents[2] / "src" / "relay" / "gui"
WIDGET_FILES = ("app.py", "window.py", "pages.py", "dialogs.py", "widgets.py", "selfcheck.py")

# Tudo que existe na libadwaita 1.5 (Ubuntu 24.04) e que a interface pode usar.
# Adw.Spinner, Adw.ButtonRow, Adw.WrapBox, Adw.ToggleGroup etc. são de versões
# mais novas e ficam de fora de propósito.
ADW_15 = {
    "AboutDialog",
    "ActionRow",
    "AlertDialog",
    "Application",
    "ApplicationWindow",
    "Banner",
    "Clamp",
    "ComboRow",
    "Dialog",
    "EntryRow",
    "HeaderBar",
    "NavigationPage",
    "NavigationView",
    "PasswordEntryRow",
    "PreferencesDialog",
    "PreferencesGroup",
    "PreferencesPage",
    "ResponseAppearance",
    "SpinRow",
    "StatusPage",
    "SwitchRow",
    "Toast",
    "ToastOverlay",
    "ToolbarView",
    "get_major_version",
    "get_minor_version",
}


def test_only_libadwaita_15_widgets():
    used = set()
    for name in WIDGET_FILES:
        used |= set(re.findall(r"\bAdw\.([A-Za-z_]+)", (GUI / name).read_text()))
    assert used <= ADW_15, f"fora da libadwaita 1.5: {sorted(used - ADW_15)}"


def test_logic_files_do_not_touch_gi():
    for p in GUI.glob("*.py"):
        if p.name in WIDGET_FILES:
            continue
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                assert not (mod or "").startswith("gi"), f"{p.name} importa {mod}"


def test_rows_never_render_agent_text_as_markup():
    """Toda linha criada pelo helper desliga o markup antes de receber texto."""
    src = (GUI / "widgets.py").read_text()
    body = src.split("def row(", 1)[1].split("\ndef ", 1)[0]
    assert body.index("set_use_markup(False)") < body.index("set_title(")
    for name in ("pages.py", "dialogs.py"):
        text = (GUI / name).read_text()
        assert "Adw.ActionRow(" not in text, f"{name}: use o helper row(), que desliga o markup"


def test_gui_without_gtk_explains_what_to_install():
    import os
    import subprocess
    import sys

    probe = "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')"
    if subprocess.run([sys.executable, "-c", probe], capture_output=True).returncode == 0:
        import pytest

        pytest.skip("aqui tem GTK 4, então a mensagem não aparece")
    env = {**os.environ, "PYTHONPATH": str(GUI.parents[1])}
    r = subprocess.run(
        [sys.executable, "-c", "import sys; from relay.gui.app import main; sys.exit(main(['x']))"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" in r.stderr
