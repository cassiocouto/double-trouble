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
from .params import ParamSpec, parse_schema
from .safety import OpenedCommand, open_command_file

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ICON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 3600
MAX_DESCRIPTION = 200
MAX_DETAILS = 2000
MAX_CATEGORY = 40

METADATA_NAMES = ("ACEITA_ARGS", "TIMEOUT", "PARAMETROS", "CONFIRMAR", "ICONE", "CATEGORIA")


@dataclass
class CommandMeta:
    nome: str
    descricao: str = ""
    detalhes: str = ""
    aceita_args: bool = False
    timeout: float = DEFAULT_TIMEOUT
    parametros: list[ParamSpec] | None = None
    confirmar: bool = False
    icone: str = ""
    categoria: str = ""
    avisos: list[str] = field(default_factory=list)

    def to_wire(self) -> dict:
        data = asdict(self)
        data["parametros"] = None if self.parametros is None else [p.to_wire() for p in self.parametros]
        for key in ("avisos", "detalhes", "icone", "categoria"):
            if not data[key]:
                data.pop(key)
        if data["parametros"] is None:
            data.pop("parametros")
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
        first, _, rest = doc.partition("\n")
        meta.descricao = first.strip()[:MAX_DESCRIPTION]
        meta.detalhes = rest.strip()[:MAX_DETAILS]

    for key, node in _literal_assignments(tree).items():
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            if key == "PARAMETROS":
                # Sem o esquema não dá pra validar nada, então o comando fica bloqueado.
                raise CommandError(f"'{name}' tem PARAMETROS que não é um valor literal") from None
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
        elif key == "PARAMETROS":
            # Erro no esquema bloqueia o comando. Ignorar faria ele rodar sem as checagens.
            try:
                meta.parametros = parse_schema(value)
            except ValueError as e:
                raise CommandError(f"'{name}' tem PARAMETROS inválido ({e})") from None
        elif key == "CONFIRMAR":
            if isinstance(value, bool):
                meta.confirmar = value
            else:
                meta.avisos.append("CONFIRMAR precisa ser True ou False")
        elif key == "ICONE":
            if isinstance(value, str) and ICON_RE.fullmatch(value):
                meta.icone = value
            else:
                meta.avisos.append("ICONE precisa ser um nome de ícone do tema, como 'computer-symbolic'")
        elif key == "CATEGORIA":
            if isinstance(value, str) and 0 < len(value) <= MAX_CATEGORY and not _CONTROL.search(value):
                meta.categoria = value.strip()
            else:
                meta.avisos.append(f"CATEGORIA precisa ser um texto de até {MAX_CATEGORY} caracteres")
    if meta.parametros is not None and meta.aceita_args:
        meta.aceita_args = False
        meta.avisos.append("com PARAMETROS, ACEITA_ARGS é ignorado (os valores vão pelo stdin)")
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
