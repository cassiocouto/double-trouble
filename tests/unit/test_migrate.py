"""Migração do v1: o config antigo fica intacto, o serviço antigo para."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ponte.cli import migrate
from ponte.core import config as config_mod
from ponte.core import paths
from ponte.core.errors import ConfigError

V1_UNIT = """[Unit]
Description=Ponte entre os desktops da Cass

[Service]
ExecStart=/usr/bin/python3 /home/cass/bin/ponte.py servir
Restart=always
"""


@pytest.fixture
def v1_setup(base, make_cmd, tmp_path, monkeypatch):
    secret = os.urandom(32).hex()
    v1 = {
        "nome": "cass-dell-brasil",
        "escutar": "100.101.102.103",
        "porta": 47800,
        "segredo": secret,
        "permitidos": ["100.64.0.0/10", "fd7a:115c:a1e0::/48", "127.0.0.0/8", "::1/128"],
        "maquinas": {"cass-dell-g15-5511": {"endereco": "cass-dell-g15-5511", "porta": 47800}},
    }
    path = base / "config.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(v1, f, indent=2)
    make_cmd("status", '"""v1."""\nprint(1)\n')
    unit_dir = Path(os.environ["HOME"]) / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "ponte.service").write_text(V1_UNIT)
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    os.chmod(fake, 0o700)
    monkeypatch.setenv("PONTE_SYSTEMCTL", str(fake))
    return path, secret, log


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_plan_maps_v1(v1_setup):
    _path, secret, _ = v1_setup
    plan = migrate.make_plan()
    cfg = plan.new
    assert cfg.name == "cass-dell-brasil"
    assert cfg.agent.listen == "tailscale"
    assert cfg.agent.allowed == ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]
    g15 = cfg.machines["cass-dell-g15-5511"]
    assert (g15.address, g15.port, g15.secret.hex()) == ("cass-dell-g15-5511", 47800, secret)
    assert cfg.agent.controllers["cass-dell-g15-5511"].secret.hex() == secret
    assert plan.v1_unit is not None
    assert any("parear de novo" in n for n in plan.notes)


def test_apply_keeps_v1_intact(v1_setup, base):
    path, _, log = v1_setup
    before = digest(path)
    commands_before = {p.name: digest(p) for p in (base / "comandos").iterdir()}
    done = migrate.apply(migrate.make_plan())
    assert digest(path) == before, "config.json do v1 não pode mudar"
    assert {p.name: digest(p) for p in (base / "comandos").iterdir()} == commands_before
    backups = list(base.glob("config.json.v1-backup-*"))
    assert len(backups) == 1 and digest(backups[0]) == before
    assert oct(os.stat(backups[0]).st_mode & 0o777) == "0o600"
    loaded = config_mod.load(paths.config_file())
    assert loaded.name == "cass-dell-brasil"
    assert log.read_text().splitlines() == ["--user stop ponte.service", "--user disable ponte.service"]
    assert any("ok" in line for line in done)


def test_apply_refuses_to_overwrite_v2(v1_setup):
    migrate.apply(migrate.make_plan())
    with pytest.raises(ConfigError, match="já existe"):
        migrate.apply(migrate.make_plan())
    done = migrate.apply(migrate.make_plan(), force=True)
    assert any("backup do config v2" in line for line in done)


def test_keep_v1_service(v1_setup):
    _, _, log = v1_setup
    migrate.apply(migrate.make_plan(), stop_v1=False)
    assert not log.exists()


def test_unit_detection_only_matches_v1(v1_setup):
    unit = Path(os.environ["HOME"]) / ".config" / "systemd" / "user" / "ponte.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/outra-coisa\n")
    assert migrate.find_v1_unit() is None


def test_v1_config_must_be_private(v1_setup):
    path, _, _ = v1_setup
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        migrate.make_plan()


def test_cli_migrar_asks_without_tty(v1_setup, capsys):
    from ponte.cli.main import main

    with pytest.raises(SystemExit) as e:
        main(["migrar"])
    assert e.value.code == 2
    assert not paths.config_file().exists()
    main(["migrar", "--sim"])
    assert paths.config_file().exists()


def test_cli_refuses_secret_on_argv(base, capsys):
    from ponte.cli.main import main
    from ponte.core import config as config_mod

    config_mod.save(config_mod.default_config("a"))
    for argv in (
        ["maquina", "adicionar", "g15", "--segredo", "ab" * 32],
        ["agente", "autorizar", "g15", "--segredo", "ab" * 32],
    ):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2
        assert "ps" in capsys.readouterr().err
    assert config_mod.load().machines == {}
