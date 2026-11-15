"""Checagens da interface que não precisam de tela nem de GTK instalado."""

from __future__ import annotations

import ast
import re
from pathlib import Path

GUI = Path(__file__).resolve().parents[2] / "src" / "ponte" / "gui"
WIDGET_FILES = ("app.py", "window.py", "pages.py", "dialogs.py", "widgets.py")

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
