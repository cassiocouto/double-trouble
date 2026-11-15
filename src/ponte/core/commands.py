"""Comandos: nomes, metadados lidos com ast e resolução segura (invariantes 1, 2 e 7).

O arquivo de um comando nunca é importado nem executado pra ler metadados.
Ele é lido como texto, passado pelo ast.parse, e só atribuições simples no
topo do módulo com valores literais contam.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .errors import CommandError
from .safety import OpenedCommand, open_command_file

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 3600
MAX_DESCRIPTION = 200

METADATA_NAMES = ("ACEITA_ARGS", "TIMEOUT")


@dataclass
class CommandMeta:
    nome: str
    descricao: str = ""
    aceita_args: bool = False
    timeout: float = DEFAULT_TIMEOUT
    avisos: list[str] = field(default_factory=list)

    def to_wire(self) -> dict:
        data = asdict(self)
        if not data["avisos"]:
            data.pop("avisos")
        return data


def valid_name(name: object) -> bool:
    return isinstance(name, str) and NAME_RE.fullmatch(name) is not None


def _literal_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Atribuições simples no topo do módulo, a última vence (igual ao Python)."""
    found: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in METADATA_NAMES:
            found[target.id] = value
    return found


def parse_metadata(name: str, source: bytes) -> CommandMeta:
    """Lê os metadados de um comando sem executar nada."""
    try:
        text = source.decode("utf-8")
        tree = ast.parse(text, filename=f"{name}.py")
    except UnicodeDecodeError:
        raise CommandError(f"'{name}' não é texto UTF-8") from None
    except (SyntaxError, ValueError) as e:
        line = getattr(e, "lineno", None)
        where = f" na linha {line}" if line else ""
        raise CommandError(f"'{name}' tem erro de sintaxe{where}") from None
    except RecursionError:
        raise CommandError(f"'{name}' é complexo demais pra ler") from None

    meta = CommandMeta(nome=name)
    doc = (ast.get_docstring(tree) or "").strip()
    if doc:
        meta.descricao = doc.splitlines()[0][:MAX_DESCRIPTION]

    for key, node in _literal_assignments(tree).items():
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            meta.avisos.append(f"{key} não é um valor literal e foi ignorado")
            continue
        if key == "ACEITA_ARGS":
            if isinstance(value, bool):
                meta.aceita_args = value
            else:
                meta.avisos.append("ACEITA_ARGS precisa ser True ou False")
        elif key == "TIMEOUT":
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= MAX_TIMEOUT:
                meta.timeout = value
            else:
                meta.avisos.append(f"TIMEOUT precisa ser um número entre 0 e {MAX_TIMEOUT}")
    return meta


@dataclass
class ResolvedCommand:
    meta: CommandMeta
    opened: OpenedCommand

    def close(self) -> None:
        self.opened.close()


def resolve(name: object, base: Path | None = None) -> ResolvedCommand:
    """Valida o nome, abre o arquivo conferindo dono e modo, e lê os metadados.

    Quem chama precisa fechar o resultado (close) depois de executar.
    """
    if not valid_name(name):
        raise CommandError("nome de comando inválido")
    assert isinstance(name, str)
    opened = open_command_file(base or paths.base_dir(), name)
    try:
        meta = parse_metadata(name, opened.source)
    except BaseException:
        opened.close()
        raise
    return ResolvedCommand(meta=meta, opened=opened)


def inspect(name: str, base: Path | None = None) -> CommandMeta:
    """Metadados de um comando, sem deixar nada aberto."""
    resolved = resolve(name, base)
    resolved.close()
    return resolved.meta


def list_commands(base: Path | None = None) -> list[dict]:
    """Todos os comandos da pasta. Os bloqueados vêm com o motivo."""
    base = base or paths.base_dir()
    folder = base / "comandos"
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.py")):
        if not valid_name(p.stem):
            continue
        try:
            out.append(inspect(p.stem, base).to_wire())
        except CommandError as e:
            out.append({"nome": p.stem, "bloqueado": str(e)})
    return out
