"""Formulário gerado a partir do que o agente listou (PARAMETROS ou ACEITA_ARGS).

Nunca existe campo de comando livre. Os campos saem da lista do agente, e a
validação usa o mesmo código do agente (core.params). O agente valida de novo
de qualquer jeito.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ParamError
from ..core.params import ParamSpec, parse_schema

MAX_ARGS = 32
MAX_ARG_CHARS = 4096
NONE_CHOICE = "(nenhuma)"


@dataclass
class Field:
    """Um campo da tela. `kind` diz qual widget usar."""

    name: str
    kind: str  # texto, texto_longo, segredo, inteiro, inteiro_opcional, booleano, escolha, argumentos
    label: str
    help: str = ""
    required: bool = False
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    options: list[str] = field(default_factory=list)
    spec: ParamSpec | None = None


class FormModel:
    def __init__(self, command: dict):
        self.command = command
        self.fields: list[Field] = []
        self.uses_params = command.get("parametros") is not None
        self.uses_args = bool(command.get("aceita_args")) and not self.uses_params
        self.error: str | None = None
        if self.uses_params:
            try:
                self.specs = parse_schema(command.get("parametros") or [])
            except ValueError as e:
                # O agente mandou um esquema que nem ele aceitaria. Não deixa rodar.
                self.specs = []
                self.error = f"o esquema de parâmetros veio inválido ({e})"
            for spec in self.specs:
                self.fields.append(self._field_for(spec))
        elif self.uses_args:
            self.specs = []
            self.fields.append(
                Field(
                    name="args",
                    kind="argumentos",
                    label="Argumentos",
                    help="Um argumento por linha. Eles vão pro comando como estão, sem shell.",
                )
            )
        else:
            self.specs = []

    @staticmethod
    def _field_for(spec: ParamSpec) -> Field:
        label = spec.rotulo or spec.nome
        f = Field(
            name=spec.nome,
            kind=spec.tipo,
            label=label,
            help=spec.ajuda,
            required=spec.obrigatorio,
            default=spec.padrao,
            minimum=spec.min,
            maximum=spec.max,
            options=list(spec.opcoes),
            spec=spec,
        )
        if spec.tipo == "texto":
            if spec.segredo:
                f.kind = "segredo"
            elif spec.multilinha:
                f.kind = "texto_longo"
        elif spec.tipo == "inteiro" and spec.padrao is None and not spec.obrigatorio:
            f.kind = "inteiro_opcional"  # pode ficar vazio, então não dá pra usar SpinRow
        elif spec.tipo == "escolha" and not spec.obrigatorio and spec.padrao is None:
            f.options = [NONE_CHOICE, *spec.opcoes]
        return f

    @property
    def runnable(self) -> bool:
        return "bloqueado" not in self.command and self.error is None

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.command.get("confirmar"))

    def initial_values(self, previous: dict | None = None) -> dict[str, Any]:
        """Valores pra preencher a tela (padrões, ou os de uma execução anterior)."""
        previous = previous or {}
        values: dict[str, Any] = {}
        for f in self.fields:
            old = previous.get(f.name)
            if f.kind == "segredo":
                values[f.name] = ""  # segredo nunca é guardado nem preenchido
            elif f.kind == "argumentos":
                values[f.name] = "\n".join(old) if isinstance(old, list) else ""
            elif old is not None and old != "***":
                values[f.name] = old
            elif f.default is not None:
                values[f.name] = f.default
            elif f.kind == "booleano":
                values[f.name] = False
            elif f.kind == "escolha":
                values[f.name] = f.options[0] if f.options else None
            elif f.kind == "inteiro":
                values[f.name] = f.minimum if f.minimum is not None else 0
            else:
                values[f.name] = ""
        return values

    def collect(self, values: dict[str, Any]) -> tuple[dict | None, list[str] | None, dict[str, str]]:
        """Converte o que está na tela em (parametros, args, erros por campo)."""
        errors: dict[str, str] = {}
        if self.uses_args:
            raw = str(values.get("args") or "")
            args = [line for line in raw.split("\n") if line != ""]
            if len(args) > MAX_ARGS:
                errors["args"] = f"No máximo {MAX_ARGS} argumentos"
            elif any(len(a) > MAX_ARG_CHARS for a in args):
                errors["args"] = f"Cada argumento pode ter até {MAX_ARG_CHARS} caracteres"
            elif any("\x00" in a for a in args):
                errors["args"] = "Argumento com byte nulo"
            return None, (args if not errors else None), errors
        if not self.uses_params:
            return None, None, errors
        params: dict[str, Any] = {}
        for f in self.fields:
            raw = values.get(f.name)
            try:
                value = self._convert(f, raw)
                if value is None:
                    if f.required and f.default is None:
                        raise ParamError("Campo obrigatório")
                    continue
                assert f.spec is not None
                params[f.name] = f.spec.check(value)
            except ParamError as e:
                msg = str(e)
                errors[f.name] = msg[:1].upper() + msg[1:]
        return (params if not errors else None), None, errors

    @staticmethod
    def _convert(f: Field, raw: Any) -> Any:
        if f.kind in ("texto", "texto_longo", "segredo"):
            text = "" if raw is None else str(raw)
            return None if text == "" else text
        if f.kind == "inteiro":
            if isinstance(raw, bool):
                raise ParamError("Precisa ser um número inteiro")
            if isinstance(raw, float):
                if not raw.is_integer():
                    raise ParamError("Precisa ser um número inteiro")
                return int(raw)
            return raw
        if f.kind == "inteiro_opcional":
            text = "" if raw is None else str(raw).strip()
            if text == "":
                return None
            try:
                return int(text, 10)
            except ValueError:
                raise ParamError("Precisa ser um número inteiro") from None
        if f.kind == "booleano":
            return bool(raw)
        if f.kind == "escolha":
            if raw in (None, NONE_CHOICE):
                return None
            return raw
        return raw


def remember_values(form: FormModel, params: dict | None, args: list[str] | None) -> dict:
    """O que guardar no histórico: segredos viram ***."""
    if args is not None:
        return {"args": list(args)}
    out = {}
    for f in form.fields:
        if params and f.name in params:
            out[f.name] = "***" if f.kind == "segredo" else params[f.name]
    return out
