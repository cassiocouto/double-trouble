"""Cifra o bloco pro programa 'tttttt' (o autotyper que só aceita texto cifrado).

O 'tttttt' roda no executor e só digita texto cifrado. A gente cifra aqui, no
controlador, com a senha compartilhada, chamando o openssl do sistema. O
'tttttt' exige esta receita exata (AES-256-CBC, PBKDF2 com 600000 voltas e
sha256, base64 numa linha só), senão recusa. O outro lado abre com a MESMA
senha:

    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -a -A -pass pass:SENHA

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
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import ConfigError, RelayError
from .safety import read_private_text, write_private
from .strictjson import loads_strict

PASS_ENV = "RELAY_TTTTTT_PASS"  # noqa: S105 (nome da variável de ambiente, não a senha)
# Receita exigida pelo tttttt: 600000 voltas de PBKDF2 com sha256, base64 numa
# linha só (-a -A). A senha vem de env:, nunca de argumento.
OPENSSL_ENCRYPT = [
    "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256",
    "-salt", "-a", "-A", "-pass", f"env:{PASS_ENV}",
]  # fmt: skip
TIMEOUT = 30
DEFAULT_CPS = 12  # caracteres por segundo que o tttttt digita (dá pra ajustar nas Configurações)


@dataclass
class AutotypeSettings:
    senha: str = ""
    cps: int = DEFAULT_CPS


def settings_path() -> Path:
    return paths.base_dir() / "tttttt.json"


def load_settings(path: Path | None = None) -> AutotypeSettings:
    path = path or settings_path()
    if not path.exists():
        return AutotypeSettings()
    data = loads_strict(read_private_text(path, "o config do tttttt"))
    if not isinstance(data, dict):
        raise ConfigError("o config do tttttt precisa ser um objeto JSON")
    senha = data.get("senha", "")
    if not isinstance(senha, str):
        raise ConfigError("a senha do tttttt precisa ser texto")
    cps = data.get("cps", DEFAULT_CPS)
    if not isinstance(cps, int) or isinstance(cps, bool) or cps < 1:
        cps = DEFAULT_CPS
    return AutotypeSettings(senha=senha, cps=cps)


def save_settings(s: AutotypeSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    paths.ensure_private_dir(path.parent)
    write_private(path, json.dumps({"senha": s.senha, "cps": s.cps}) + "\n", mode=0o600)


# Igual ao TIMEOUT do comando autodigitar (o teto do agente): serve pra avisar se
# a estimativa chega perto, o que só acontece com bloco enorme e velocidade baixa.
COMMAND_TIMEOUT = 3600


def estimate_seconds(n_chars: int, atraso: int = 0, cps: int = DEFAULT_CPS) -> int:
    """Quanto o tttttt deve levar pra digitar: a espera mais os caracteres pela velocidade."""
    cps = cps if cps and cps > 0 else DEFAULT_CPS
    return int(atraso) + round(max(0, n_chars) / cps)


def format_eta(seconds: int) -> str:
    if seconds < 90:
        return f"~{seconds}s"
    m, s = divmod(int(seconds), 60)
    return f"~{m}min{s:02d}s"


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
