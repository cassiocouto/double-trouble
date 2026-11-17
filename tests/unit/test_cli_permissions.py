"""relay agente permissoes: o que uma máquina pareada pode rodar aqui."""

from __future__ import annotations

import os

import pytest

from relay.cli.main import main
from relay.core import config as config_mod

G15 = "cass-dell-g15-5511"


@pytest.fixture
def thinker(base, make_cmd):
    cfg = config_mod.default_config("cass-hp-elitedesk-800-g6")
    cfg.agent.controllers[G15] = config_mod.ControllerEntry(G15, os.urandom(32))
    config_mod.save(cfg)
    make_cmd("notificar", "print(1)\n")
    make_cmd("status", "print(1)\n")
    return cfg


def allowed():
    return config_mod.load().agent.controllers[G15].commands


def test_show_default_is_everything(thinker, capsys):
    main(["agente", "permissoes", G15])
    assert "todos os comandos" in capsys.readouterr().out
    assert allowed() is None


def test_limit_then_free_again(thinker, capsys):
    main(["agente", "permissoes", G15, "notificar", "status"])
    assert allowed() == ["notificar", "status"]
    main(["agente", "permissoes", G15, "notificar,status,notificar"])
    assert allowed() == ["notificar", "status"]
    main(["agente", "permissoes", G15, "*"])
    assert allowed() is None


def test_warns_about_commands_that_do_not_exist_yet(thinker, capsys):
    main(["agente", "permissoes", G15, "backup"])
    assert allowed() == ["backup"]
    assert "ainda não existe aqui: backup" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["../x", "Status", "a b"])
def test_rejects_bad_names(thinker, bad):
    with pytest.raises(SystemExit) as e:
        main(["agente", "permissoes", G15, bad])
    assert e.value.code == 2
    assert allowed() is None


def test_unknown_machine(thinker, capsys):
    with pytest.raises(SystemExit) as e:
        main(["agente", "permissoes", "cass-dell-brasil", "status"])
    assert e.value.code == 2
    assert "não está pareada" in capsys.readouterr().err


def test_agent_honors_the_new_limit(agent, monkeypatch):
    """A permissão gravada pela CLI vale no agente sem reiniciar nada."""
    from relay.core.errors import RemoteError

    h = agent()
    h.make_cmd("status", "print('ok')\n")
    h.make_cmd("travar", "print('nao')\n")
    monkeypatch.setenv("RELAY_DIR", str(h.base))
    main(["agente", "permissoes", h.controller, "status"])
    assert h.client().run("status").text_stdout == "ok\n"
    with pytest.raises(RemoteError, match="não deixa"):
        h.client().run("travar")
