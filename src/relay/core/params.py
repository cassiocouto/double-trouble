"""PARAMETROS: parâmetros tipados de um comando (invariante 8).

O esquema vem do arquivo do comando (lido com ast, como literal). O agente
valida cada valor contra o esquema antes de rodar. O comando recebe os
valores já validados como um objeto JSON no stdin.

Exemplo no arquivo do comando

    PARAMETROS = [
        {"nome": "texto", "tipo": "texto", "rotulo": "Mensagem", "obrigatorio": True, "max": 200},
        {"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 10, "padrao": 1},
        {"nome": "urgente", "tipo": "booleano"},
        {"nome": "modo", "tipo": "escolha", "opcoes": ["rapido", "lento"], "padrao": "rapido"},
    ]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from .errors import ParamError

TYPES = ("texto", "inteiro", "booleano", "escolha")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

MAX_PARAMS = 32
MAX_TEXT = 4096
MAX_LABEL = 80
MAX_HELP = 300
MAX_OPTIONS = 100
MAX_OPTION_CHARS = 200
MAX_REGEX = 500
INT_LIMIT = 2**53  # cabe num número JSON sem perder precisão
REGEX_TIMEOUT = 1.0  # segundos pra conferir um texto contra a regex do comando

# A regex roda num processo separado: o `re` do Python segura o GIL, então uma
# regex que trava (backtracking) congelaria o agente inteiro, inclusive os
# timeouts dos outros jobs. Num processo à parte ela só atrasa esse pedido.
_MATCHER = (
    "import json, re, sys\nd = json.loads(sys.stdin.read())\nsys.exit(0 if re.fullmatch(d['p'], d['v']) else 1)\n"
)


def regex_fullmatch(pattern: str, value: str, timeout: float | None = None) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _MATCHER],
            input=json.dumps({"p": pattern, "v": value}),
            capture_output=True,
            text=True,
            timeout=REGEX_TIMEOUT if timeout is None else timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ParamError("a conferência do formato demorou demais (a regex do comando é lenta)") from None
    except OSError as e:
        raise ParamError(f"não consegui conferir o formato ({e.strerror or e})") from None
    if r.returncode not in (0, 1):
        raise ParamError("não consegui conferir o formato")
    return r.returncode == 0


COMMON_KEYS = {"nome", "tipo", "rotulo", "ajuda", "obrigatorio", "padrao"}
KEYS_BY_TYPE = {
    "texto": COMMON_KEYS | {"min", "max", "regex", "multilinha", "segredo"},
    "inteiro": COMMON_KEYS | {"min", "max"},
    "booleano": COMMON_KEYS,
    "escolha": COMMON_KEYS | {"opcoes"},
}

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CONTROL_SINGLE_LINE = re.compile(r"[\x00-\x1f\x7f]")


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass
class ParamSpec:
    nome: str
    tipo: str
    rotulo: str = ""
    ajuda: str = ""
    obrigatorio: bool = False
    padrao: Any = None
    min: int | None = None
    max: int | None = None
    regex: str | None = None
    multilinha: bool = False
    segredo: bool = False
    opcoes: list[str] = field(default_factory=list)
    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    def to_wire(self) -> dict:
        out: dict[str, Any] = {"nome": self.nome, "tipo": self.tipo, "rotulo": self.rotulo or self.nome}
        if self.ajuda:
            out["ajuda"] = self.ajuda
        out["obrigatorio"] = self.obrigatorio
        if self.padrao is not None:
            out["padrao"] = self.padrao
        if self.tipo in ("texto", "inteiro"):
            if self.min is not None:
                out["min"] = self.min
            if self.max is not None:
                out["max"] = self.max
        if self.tipo == "texto":
            if self.regex:
                out["regex"] = self.regex
            if self.multilinha:
                out["multilinha"] = True
            if self.segredo:
                out["segredo"] = True
        if self.tipo == "escolha":
            out["opcoes"] = list(self.opcoes)
        return out

    # -- validação de um valor --------------------------------------------
    def check(self, value: Any) -> Any:
        """Valida e devolve o valor normalizado, ou levanta ParamError."""
        label = self.rotulo or self.nome
        if self.tipo == "texto":
            if not isinstance(value, str):
                raise ParamError(f"'{label}' precisa ser texto")
            if "\x00" in value:
                raise ParamError(f"'{label}' tem um byte nulo")
            if self.obrigatorio and value == "":
                raise ParamError(f"'{label}' não pode ficar vazio")
            bad = _CONTROL if self.multilinha else _CONTROL_SINGLE_LINE
            if bad.search(value):
                raise ParamError(f"'{label}' tem caracteres de controle")
            limit = self.max if self.max is not None else MAX_TEXT
            if len(value) > limit:
                raise ParamError(f"'{label}' passou de {limit} caracteres")
            if self.min is not None and len(value) < self.min:
                raise ParamError(f"'{label}' precisa de pelo menos {self.min} caracteres")
            if self.regex is not None and not regex_fullmatch(self.regex, value):
                raise ParamError(f"'{label}' não está no formato esperado")
            return value
        if self.tipo == "inteiro":
            if not _is_int(value):
                raise ParamError(f"'{label}' precisa ser um número inteiro")
            lo = self.min if self.min is not None else -INT_LIMIT
            hi = self.max if self.max is not None else INT_LIMIT
            if not lo <= value <= hi:
                raise ParamError(f"'{label}' precisa estar entre {lo} e {hi}")
            return value
        if self.tipo == "booleano":
            if not isinstance(value, bool):
                raise ParamError(f"'{label}' precisa ser verdadeiro ou falso")
            return value
        if self.tipo == "escolha":
            if not isinstance(value, str) or value not in self.opcoes:
                raise ParamError(f"'{label}' precisa ser uma destas opções: {', '.join(self.opcoes)}")
            return value
        raise ParamError(f"tipo desconhecido em '{label}'")  # pragma: no cover

    def empty_value(self) -> Any:
        """Valor de um parâmetro opcional que não veio e não tem padrão."""
        return False if self.tipo == "booleano" else None


def _schema_error(index: int, msg: str) -> ValueError:
    return ValueError(f"PARAMETROS[{index}]: {msg}")


def _text(value: object, limit: int, index: int, key: str) -> str:
    if not isinstance(value, str) or len(value) > limit or _CONTROL_SINGLE_LINE.search(value):
        raise _schema_error(index, f"'{key}' precisa ser texto curto (até {limit} caracteres)")
    return value


def parse_schema(raw: object) -> list[ParamSpec]:
    """Confere o PARAMETROS vindo do arquivo. Qualquer erro bloqueia o comando."""
    if not isinstance(raw, list):
        raise ValueError("PARAMETROS precisa ser uma lista")
    if len(raw) > MAX_PARAMS:
        raise ValueError(f"PARAMETROS tem mais de {MAX_PARAMS} itens")
    specs: list[ParamSpec] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _schema_error(i, "cada item precisa ser um dicionário")
        name = item.get("nome")
        if not isinstance(name, str) or not PARAM_NAME_RE.fullmatch(name):
            raise _schema_error(i, "'nome' precisa ser minúsculo, começar com letra (a-z, 0-9, _)")
        if name in seen:
            raise _schema_error(i, f"o nome '{name}' aparece duas vezes")
        seen.add(name)
        kind = item.get("tipo")
        if kind not in TYPES:
            raise _schema_error(i, f"'tipo' precisa ser um de {', '.join(TYPES)}")
        unknown = set(item) - KEYS_BY_TYPE[kind]
        if unknown:
            raise _schema_error(i, f"chave desconhecida pra '{kind}': {', '.join(sorted(unknown))}")
        spec = ParamSpec(nome=name, tipo=kind)
        if "rotulo" in item:
            spec.rotulo = _text(item["rotulo"], MAX_LABEL, i, "rotulo")
        if "ajuda" in item:
            spec.ajuda = _text(item["ajuda"], MAX_HELP, i, "ajuda")
        for flag in ("obrigatorio", "multilinha", "segredo"):
            if flag in item:
                if not isinstance(item[flag], bool):
                    raise _schema_error(i, f"'{flag}' precisa ser True ou False")
                setattr(spec, flag, item[flag])
        for bound in ("min", "max"):
            if bound in item:
                v = item[bound]
                if not _is_int(v) or abs(v) > INT_LIMIT:
                    raise _schema_error(i, f"'{bound}' precisa ser um inteiro")
                setattr(spec, bound, v)
        if kind == "texto":
            if spec.min is not None and spec.min < 0:
                raise _schema_error(i, "'min' de texto não pode ser negativo")
            if spec.max is not None and not 0 <= spec.max <= MAX_TEXT:
                raise _schema_error(i, f"'max' de texto vai de 0 a {MAX_TEXT}")
            if "regex" in item:
                rx = item["regex"]
                if not isinstance(rx, str) or not rx or len(rx) > MAX_REGEX:
                    raise _schema_error(i, f"'regex' precisa ser texto de até {MAX_REGEX} caracteres")
                try:
                    spec._compiled = re.compile(rx)
                except re.error as e:
                    raise _schema_error(i, f"'regex' inválida ({e})") from None
                spec.regex = rx
        if spec.min is not None and spec.max is not None and spec.min > spec.max:
            raise _schema_error(i, "'min' é maior que 'max'")
        if kind == "escolha":
            opts = item.get("opcoes")
            if (
                not isinstance(opts, list)
                or not opts
                or len(opts) > MAX_OPTIONS
                or not all(isinstance(o, str) and 0 < len(o) <= MAX_OPTION_CHARS for o in opts)
                or len(set(opts)) != len(opts)
                or any(_CONTROL_SINGLE_LINE.search(o) for o in opts)
            ):
                raise _schema_error(i, "'opcoes' precisa ser uma lista de textos diferentes, sem vazios")
            spec.opcoes = list(opts)
        if "padrao" in item:
            try:
                spec.padrao = spec.check(item["padrao"])
            except ParamError as e:
                raise _schema_error(i, f"o 'padrao' não passa na própria regra ({e})") from None
        specs.append(spec)
    return specs


def validate_values(specs: list[ParamSpec], values: object) -> dict[str, Any]:
    """Valida o que o controlador mandou e devolve o objeto completo pro comando."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ParamError("os parâmetros precisam ser um objeto")
    known = {s.nome for s in specs}
    extra = [k for k in values if k not in known]
    if extra:
        shown = ", ".join(repr(str(k)[:40]) for k in extra[:5])
        raise ParamError(f"parâmetro desconhecido: {shown}")
    out: dict[str, Any] = {}
    for spec in specs:
        if spec.nome in values and values[spec.nome] is not None:
            out[spec.nome] = spec.check(values[spec.nome])
        elif spec.padrao is not None:
            out[spec.nome] = spec.padrao
        elif spec.obrigatorio:
            raise ParamError(f"falta o parâmetro obrigatório '{spec.rotulo or spec.nome}'")
        else:
            out[spec.nome] = spec.empty_value()
    return out


def parse_cli_value(spec: ParamSpec | dict, text: str) -> Any:
    """Converte texto digitado na CLI pro tipo do parâmetro (o agente valida de novo)."""
    kind = spec.tipo if isinstance(spec, ParamSpec) else spec.get("tipo")
    name = spec.nome if isinstance(spec, ParamSpec) else spec.get("nome")
    if kind == "inteiro":
        try:
            return int(text.strip(), 10)
        except ValueError:
            raise ParamError(f"'{name}' precisa ser um número inteiro") from None
    if kind == "booleano":
        low = text.strip().lower()
        if low in ("1", "sim", "s", "true", "verdadeiro", "v", "yes", "y"):
            return True
        if low in ("0", "nao", "não", "n", "false", "falso", "f", "no"):
            return False
        raise ParamError(f"'{name}' precisa ser sim ou não")
    return text
