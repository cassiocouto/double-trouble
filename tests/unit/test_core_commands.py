"""Metadados lidos com ast e resolução segura de comandos (invariantes 1, 2 e 7)."""

from __future__ import annotations

import os

import pytest

from ponte.core import commands, safety
from ponte.core.errors import CommandError


def test_metadata_defaults(make_cmd):
    make_cmd("simples", 'print("oi")\n')
    meta = commands.inspect("simples")
    assert meta.descricao == ""
    assert meta.aceita_args is False
    assert meta.timeout == 60


def test_metadata_values(make_cmd):
    make_cmd(
        "cheio",
        '"""Primeira linha.\n\nResto ignorado."""\nACEITA_ARGS = True\nTIMEOUT: int = 15\n',
    )
    meta = commands.inspect("cheio")
    assert meta.descricao == "Primeira linha."
    assert meta.aceita_args is True
    assert meta.timeout == 15


@pytest.mark.parametrize(
    "code, field, expected",
    [
        ("ACEITA_ARGS = 1\n", "aceita_args", False),
        ("ACEITA_ARGS = 'sim'\n", "aceita_args", False),
        ("TIMEOUT = 0\n", "timeout", 60),
        ("TIMEOUT = 99999\n", "timeout", 60),
        ("TIMEOUT = True\n", "timeout", 60),
        ("TIMEOUT = -5\n", "timeout", 60),
        ("if True:\n    ACEITA_ARGS = True\n", "aceita_args", False),
        ("def f():\n    global ACEITA_ARGS\n    ACEITA_ARGS = True\n", "aceita_args", False),
    ],
)
def test_metadata_ignores_bad_values(make_cmd, code, field, expected):
    make_cmd("ruim", code)
    assert getattr(commands.inspect("ruim"), field) == expected


def test_metadata_never_executes_code(make_cmd, tmp_path):
    """Invariante 2: ler metadados nunca roda o arquivo."""
    marker = tmp_path / "rodou"
    make_cmd(
        "armadilha",
        f"import os\nos.system('touch {marker}')\n"
        f"TIMEOUT = __import__('os').system('touch {marker}') or 5\n"
        f"ACEITA_ARGS = (lambda: open({str(marker)!r}, 'w'))() is None\n",
    )
    meta = commands.inspect("armadilha")
    assert meta.timeout == 60
    assert meta.aceita_args is False
    assert meta.avisos  # avisa que ignorou valores não literais
    assert not marker.exists()
    commands.list_commands()
    assert not marker.exists()


def test_syntax_error_blocks(make_cmd):
    make_cmd("quebrado", "def (:\n")
    with pytest.raises(CommandError, match="erro de sintaxe"):
        commands.inspect("quebrado")


def test_non_utf8_blocks(base):
    p = base / "comandos" / "binario.py"
    p.write_bytes(b"\xff\xfe\x00")
    os.chmod(p, 0o600)
    with pytest.raises(CommandError, match="UTF-8"):
        commands.inspect("binario")


@pytest.mark.parametrize("name", ["../x", "a/b", "X", "-a", "_a", "a" * 65, "a.b", "", None, 3, "a\x00"])
def test_bad_names(base, name):
    with pytest.raises(CommandError, match="nome de comando inválido"):
        commands.resolve(name)


def test_symlink_refused(make_cmd, base, tmp_path):
    target = tmp_path / "fora.py"
    target.write_text("print(1)\n")
    os.chmod(target, 0o600)
    (base / "comandos" / "link.py").symlink_to(target)
    with pytest.raises(CommandError, match="link simbólico"):
        commands.resolve("link")


def test_fifo_refused_without_hanging(base):
    os.mkfifo(base / "comandos" / "cano.py", 0o600)
    with pytest.raises(CommandError, match="arquivo comum"):
        commands.resolve("cano")


def test_directory_named_like_command_refused(base):
    (base / "comandos" / "pasta.py").mkdir(mode=0o700)
    with pytest.raises(CommandError):
        commands.resolve("pasta")


@pytest.mark.parametrize("mode", [0o602, 0o620, 0o666])
def test_writable_file_refused(make_cmd, mode):
    make_cmd("aberto", "print(1)\n", mode=mode)
    with pytest.raises(CommandError, match="permissões abertas"):
        commands.resolve("aberto")


def test_readable_by_others_is_ok(make_cmd):
    make_cmd("legivel", "print(1)\n", mode=0o644)
    commands.inspect("legivel")


@pytest.mark.parametrize("which", ["base", "comandos"])
def test_writable_dirs_refused(make_cmd, base, which):
    make_cmd("oi", "print(1)\n")
    d = base if which == "base" else base / "comandos"
    os.chmod(d, 0o775)
    try:
        with pytest.raises(CommandError, match="permissões abertas"):
            commands.resolve("oi")
    finally:
        os.chmod(d, 0o700)


def test_other_owner_refused(make_cmd, monkeypatch):
    """Simula outro dono trocando o uid esperado (funciona sem root)."""
    make_cmd("alheio", "print(1)\n")
    real = os.getuid()
    monkeypatch.setattr(safety.os, "getuid", lambda: real + 1)
    with pytest.raises(CommandError, match="outro usuário"):
        commands.resolve("alheio")


@pytest.mark.skipif(os.geteuid() != 0, reason="precisa de root pra trocar o dono")
def test_other_owner_refused_for_real(make_cmd):
    p = make_cmd("alheio", "print(1)\n")
    os.chown(p, 65534, 65534)
    with pytest.raises(CommandError, match="outro usuário"):
        commands.resolve("alheio")


def test_huge_file_refused(make_cmd):
    make_cmd("enorme", "#" * (safety.MAX_COMMAND_BYTES + 10))
    with pytest.raises(CommandError, match="grande demais"):
        commands.resolve("enorme")


def test_list_commands_includes_blocked(make_cmd):
    make_cmd("bom", '"""Bom."""\n')
    make_cmd("ruim", "def (:\n")
    make_cmd("aberto", "print(1)\n", mode=0o666)
    listed = {c["nome"]: c for c in commands.list_commands()}
    assert listed["bom"]["descricao"] == "Bom."
    assert "bloqueado" in listed["ruim"]
    assert "bloqueado" in listed["aberto"]


def test_list_commands_skips_bad_names(base, make_cmd):
    make_cmd("bom", "print(1)\n")
    (base / "comandos" / "Maiusculo.py").write_text("print(1)\n")
    (base / "comandos" / "nao-py.txt").write_text("x")
    assert [c["nome"] for c in commands.list_commands()] == ["bom"]


def test_resolved_fd_is_the_checked_file(make_cmd, base):
    """Trocar o arquivo depois de conferir não muda o que vai rodar."""
    p = make_cmd("troca", 'print("original")\n')
    resolved = commands.resolve("troca")
    try:
        p.unlink()
        p.write_text('print("trocado")\n')
        with open(f"/proc/self/fd/{resolved.opened.fd}") as f:
            assert f.read() == 'print("original")\n'
    finally:
        resolved.close()
