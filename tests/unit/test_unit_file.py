"""O unit de usuário: opções de endurecimento e checagem com systemd-analyze."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "data" / "ponte-agent.service.in"


def render(tmp_path: Path) -> Path:
    unit = tmp_path / "ponte-agent.service"
    text = TEMPLATE.read_text().replace("@VENV@", "/usr").replace("@REPO@", str(REPO))
    text = text.replace("ExecStart=/usr/bin/ponte-agent", "ExecStart=/usr/bin/true")
    unit.write_text(text)
    return unit


def test_unit_has_expected_settings():
    text = TEMPLATE.read_text()
    for line in (
        "Type=notify",
        "WatchdogSec=60",
        "KillMode=control-group",
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "RestrictNamespaces=yes",
        "UMask=0077",
        "WantedBy=default.target",
    ):
        assert line in text.splitlines(), line
    # Nada de User=root nem de opções que não funcionam em unit de usuário.
    for bad in ("User=", "ProtectSystem=", "PrivateTmp=", "CapabilityBoundingSet=", "MemoryDenyWriteExecute="):
        assert not any(line.startswith(bad) for line in text.splitlines()), bad


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="sem systemd-analyze")
def test_systemd_analyze_security(tmp_path):
    unit = render(tmp_path)
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime)}
    verify = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert "Unknown key" not in verify.stderr and "Invalid" not in verify.stderr, verify.stderr
    sec = subprocess.run(
        ["systemd-analyze", "--user", "security", "--offline=true", unit.name],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert sec.returncode == 0, sec.stderr
    passed = {line.split()[1] for line in sec.stdout.splitlines() if line.startswith("✓")}
    for name in (
        "NoNewPrivileges=",
        "LockPersonality=",
        "RestrictRealtime=",
        "RestrictSUIDSGID=",
        "SystemCallArchitectures=",
        "UMask=",
        "RestrictNamespaces=~user",
        "KeyringMode=",
    ):
        assert name in passed, f"{name} devia passar\n{sec.stdout}"
