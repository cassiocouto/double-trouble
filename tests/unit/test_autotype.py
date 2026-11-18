"""Cifra o bloco pro 'tttttt' (openssl), com transporte falso e um teste real."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from relay.core import autotype
from relay.core.errors import RelayError


def test_save_and_load_password(tmp_path):
    p = tmp_path / "tttttt.json"
    autotype.save_password("segredo 123", path=p)
    assert autotype.load_password(path=p) == "segredo 123"
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_load_password_absent_is_empty(tmp_path):
    assert autotype.load_password(path=tmp_path / "nao-existe.json") == ""


def test_encrypt_needs_a_password():
    with pytest.raises(RelayError, match="senha"):
        autotype.encrypt("oi", "")


def test_encrypt_sends_password_by_env_not_argv():
    seen = {}

    def fake(argv, stdin, env):
        seen["argv"] = argv
        seen["stdin"] = stdin
        seen["env"] = env
        return 0, b"BASE64CIFRADO\n", b""

    out = autotype.encrypt("oi mundo", "minha-senha", runner=fake)
    assert out == "BASE64CIFRADO"
    assert seen["stdin"] == b"oi mundo"
    assert seen["env"][autotype.PASS_ENV] == "minha-senha"
    # a senha nunca pode estar num argumento (o `ps` de outros usuários vê argv)
    assert all("minha-senha" not in a for a in seen["argv"])


def test_encrypt_error_bubbles_up():
    def fake(argv, stdin, env):
        return 1, b"", b"openssl: deu ruim"

    with pytest.raises(RelayError, match="openssl"):
        autotype.encrypt("oi", "senha", runner=fake)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="precisa do openssl")
def test_roundtrip_with_real_openssl():
    senha = "senha-de-teste-42"
    texto = "linha 1\nlinha 2 com acento ção\nlinha 3"
    cifrado = autotype.encrypt(texto, senha)
    # decifra como o tttttt faria, com a mesma senha
    r = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-base64", "-pass", f"pass:{senha}"],
        input=cifrado.encode(),
        capture_output=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.decode() == texto
