"""relay.command: abrir link e programa fora do agente, sempre com programas falsos."""

from __future__ import annotations

import os

import pytest

from relay import command


@pytest.fixture
def fakebin(tmp_path, monkeypatch):
    """PATH só com programas falsos: nada de verdade abre na tela."""
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    log = tmp_path / "chamadas.log"

    def make(name: str, code: int = 0, stderr: str = "") -> None:
        script = d / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\necho "{stderr}" >&2\nexit {code}\n')
        os.chmod(script, 0o700)

    make.log = log
    return make


def calls(fakebin) -> str:
    return fakebin.log.read_text() if fakebin.log.exists() else ""


def test_portal_first(monkeypatch, fakebin):
    fakebin("systemd-run")
    monkeypatch.setattr(command, "_open_via_portal", lambda uri: True)
    assert command.open_uri("https://exemplo.com") == "portal"
    assert calls(fakebin) == ""


def test_falls_back_to_systemd_with_killmode_process(monkeypatch, fakebin):
    fakebin("systemd-run")
    monkeypatch.setattr(command, "_open_via_portal", lambda uri: False)
    assert command.open_uri("https://exemplo.com/a b") == "systemd"
    line = calls(fakebin)
    assert "--user" in line and "--property=KillMode=process" in line
    assert "--unit=relay-detached-" in line and "-- xdg-open https://exemplo.com/a b" in line


def test_systemd_failure_is_reported_and_runs_directly(monkeypatch, fakebin, capsys):
    fakebin("systemd-run", code=1, stderr="sem barramento")
    fakebin("xdg-open")
    monkeypatch.setattr(command, "_open_via_portal", lambda uri: False)
    assert command.open_uri("https://exemplo.com") == "direto"
    assert "sem barramento" in capsys.readouterr().err
    import time

    for _ in range(50):
        if "xdg-open https://exemplo.com" in calls(fakebin):
            break
        time.sleep(0.05)
    assert "xdg-open https://exemplo.com" in calls(fakebin)


def test_portal_without_session_bus_says_no():
    """Sem barramento da sessão (como nos testes), o portal só responde que não deu."""
    assert command._open_via_portal("https://exemplo.com") is False


def test_detach_needs_a_command():
    with pytest.raises(ValueError):
        command.detach([])


def test_abrir_url_example_uses_the_portal_helper():
    from importlib import resources

    code = (resources.files("relay") / "data" / "exemplos" / "abrir_url.py").read_text()
    assert "open_uri(url)" in code and "detach(" not in code


def test_session_env_is_off_in_tests():
    from relay.agent.session_env import session_env

    assert session_env() == {}


def test_which_finds_program_in_user_local_bin(tmp_path, monkeypatch):
    # o serviço do systemd tem PATH curto; which() tem que achar em ~/.local/bin
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    prog = local / "tttttt"
    prog.write_text("#!/bin/sh\n")
    os.chmod(prog, 0o700)
    assert command.which("tttttt") == str(prog)


def test_which_rejects_unsafe_names_without_shell(tmp_path, monkeypatch):
    # nome com caracteres estranhos não vai pro shell de login (sem injeção)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    assert command.which("tt; rm -rf x") is None
