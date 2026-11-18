"""Atualização dos comandos de exemplo: atualiza os intactos, preserva os editados."""

from __future__ import annotations

import hashlib
import os

from relay.cli.main import install_examples
from relay.core import paths

BUNDLED = {"status", "notificar", "travar_tela", "abrir_url", "screenshot", "autodigitar"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_first_run_creates_all(base):
    created, updated = install_examples()
    assert set(created) == BUNDLED and updated == []
    folder = paths.commands_dir()
    for name in BUNDLED:
        assert (folder / f"{name}.py").exists()
        assert (folder / f".{name}.py.sha").exists()
    # rodar de novo não faz nada: já está em dia
    assert install_examples() == ([], [])


def test_pristine_example_auto_updates(base):
    install_examples()
    folder = paths.commands_dir()
    shot = folder / "screenshot.py"
    old = "print('versao antiga do exemplo')\n"
    shot.write_text(old)
    os.chmod(shot, 0o600)
    (folder / ".screenshot.py.sha").write_text(sha(old) + "\n")  # marca como intacto

    created, updated = install_examples()
    assert "screenshot" in updated and created == []
    assert shot.read_text() != old  # voltou pra versão do pacote
    assert not list(folder.glob("screenshot.py.bak-*"))  # intacto não vira backup


def test_user_edited_example_is_kept(base):
    install_examples()
    folder = paths.commands_dir()
    shot = folder / "screenshot.py"
    shot.write_text("print('meu comando')\n")  # editado: o .sha não bate mais

    created, updated = install_examples()
    assert "screenshot" not in updated
    assert shot.read_text() == "print('meu comando')\n"


def test_force_replaces_and_backs_up_edited(base):
    install_examples()
    folder = paths.commands_dir()
    shot = folder / "screenshot.py"
    shot.write_text("print('meu comando')\n")

    created, updated = install_examples(force=True)
    assert "screenshot" in updated
    assert shot.read_text() != "print('meu comando')\n"
    assert list(folder.glob("screenshot.py.bak-*"))


def test_sha_sidecars_are_not_commands(base):
    """Os arquivos de hash não contam como comandos."""
    from relay.core import commands

    install_examples()
    names = {c["nome"] for c in commands.list_commands()}
    assert names == BUNDLED
    assert not any(n.startswith(".") for n in names)
