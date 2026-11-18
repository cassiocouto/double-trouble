"""Cifra o bloco pro programa 'tttttt' (o autotyper que só aceita texto cifrado).

O 'tttttt' roda no executor e só digita texto cifrado. A gente cifra aqui, no
controlador, com a senha compartilhada, chamando o openssl do sistema
(AES-256-CBC + PBKDF2). O 'tttttt' do outro lado abre com a MESMA senha:

    openssl enc -d -aes-256-cbc -pbkdf2 -base64 -pass pass:SENHA

Em tempo de execução, só a biblioteca padrão e o openssl do sistema (como o
scrot do screenshot). A senha fica só nesta máquina, num arquivo modo 600, e vai
pro openssl por variável de ambiente (não por argumento), pra não aparecer no
`ps` de outros usuários.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import paths
from .errors import ConfigError, RelayError
from .safety import read_private_text, write_private
from .strictjson import loads_strict

PASS_ENV = "RELAY_TTTTTT_PASS"  # noqa: S105 (nome da variável de ambiente, não a senha)
OPENSSL_ENCRYPT = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-base64", "-pass", f"env:{PASS_ENV}"]
TIMEOUT = 30


def password_path() -> Path:
    return paths.base_dir() / "tttttt.json"


def load_password(path: Path | None = None) -> str:
    path = path or password_path()
    if not path.exists():
        return ""
    data = loads_strict(read_private_text(path, "a senha do tttttt"))
    if not isinstance(data, dict):
        raise ConfigError("o config do tttttt precisa ser um objeto JSON")
    senha = data.get("senha", "")
    if not isinstance(senha, str):
        raise ConfigError("a senha do tttttt precisa ser texto")
    return senha


def save_password(senha: str, path: Path | None = None) -> None:
    path = path or password_path()
    paths.ensure_private_dir(path.parent)
    write_private(path, json.dumps({"senha": senha}) + "\n", mode=0o600)


# Transporte injetável: (argv, stdin, env) -> (código, stdout, stderr). Nos
# testes a gente passa um falso, sem tocar no openssl.
Runner = Callable[[list[str], bytes, dict], tuple[int, bytes, bytes]]


def _run(argv: list[str], stdin: bytes, env: dict) -> tuple[int, bytes, bytes]:
    r = subprocess.run(argv, input=stdin, capture_output=True, timeout=TIMEOUT, env=env, check=False)
    return r.returncode, r.stdout, r.stderr


def encrypt(text: str, password: str, runner: Runner = _run) -> str:
    """Devolve o texto cifrado em base64, pronto pro tttttt abrir com a mesma senha."""
    if not password:
        raise RelayError("falta a senha do tttttt. Ponha ela nas Configurações")
    if runner is _run and shutil.which("openssl") is None:
        raise RelayError("o openssl não está instalado nesta máquina")
    env = {**os.environ, PASS_ENV: password}
    rc, out, err = runner(OPENSSL_ENCRYPT, text.encode("utf-8"), env)
    if rc != 0:
        detalhe = err.decode("utf-8", "replace").strip().splitlines()
        raise RelayError(f"não consegui cifrar com o openssl ({detalhe[-1] if detalhe else 'erro'})")
    return out.decode("ascii", "replace").strip()
