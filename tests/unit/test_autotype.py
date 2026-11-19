"""Cifra o bloco pro 'tttttt' (openssl), com transporte falso e um teste real."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from relay.core import autotype
from relay.core.errors import RelayError


def test_save_and_load_settings(tmp_path):
    p = tmp_path / "tttttt.json"
    autotype.save_settings(autotype.AutotypeSettings(senha="segredo 123", cps=30), path=p)
    loaded = autotype.load_settings(path=p)
    assert loaded.senha == "segredo 123"
    assert loaded.cps == 30
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_load_settings_absent_is_default(tmp_path):
    s = autotype.load_settings(path=tmp_path / "nao-existe.json")
    assert s.senha == ""
    assert s.cps == autotype.DEFAULT_CPS


def test_bad_cps_falls_back_to_default(tmp_path):
    p = tmp_path / "tttttt.json"
    p.write_text('{"senha": "x", "cps": 0}')
    p.chmod(0o600)
    assert autotype.load_settings(path=p).cps == autotype.DEFAULT_CPS


def test_estimate_seconds():
    # 120 caracteres a 12/s = 10s, mais 3s de espera = 13s
    assert autotype.estimate_seconds(120, atraso=3, cps=12) == 13
    assert autotype.estimate_seconds(0, atraso=5, cps=12) == 5
    assert autotype.estimate_seconds(100, atraso=0, cps=0) == round(100 / autotype.DEFAULT_CPS)


def test_format_eta():
    assert autotype.format_eta(45) == "~45s"
    assert autotype.format_eta(90) == "~1min30s"
    assert autotype.format_eta(174) == "~2min54s"


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
    assert "\n" not in cifrado  # base64 numa linha só (-A), como o tttttt quer
    # decifra com a receita exata do tttttt (600000 voltas, sha256, base64)
    recipe = ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256", "-a", "-A"]
    r = subprocess.run(
        [*recipe, "-pass", f"pass:{senha}"],
        input=cifrado.encode(),
        capture_output=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.decode() == texto
