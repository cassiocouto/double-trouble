"""Esquema PARAMETROS e validação de valores."""

from __future__ import annotations

import pytest

from ponte.core.errors import ParamError
from ponte.core.params import parse_cli_value, parse_schema, validate_values


def test_full_schema_roundtrip():
    specs = parse_schema(
        [
            {
                "nome": "t",
                "tipo": "texto",
                "rotulo": "Texto",
                "ajuda": "ajuda",
                "min": 1,
                "max": 5,
                "regex": "[a-z]+",
            },
            {"nome": "n", "tipo": "inteiro", "min": -1, "max": 1, "padrao": 0, "obrigatorio": True},
            {"nome": "b", "tipo": "booleano", "padrao": True},
            {"nome": "e", "tipo": "escolha", "opcoes": ["x", "y"]},
            {"nome": "m", "tipo": "texto", "multilinha": True, "segredo": True},
        ]
    )
    wire = [s.to_wire() for s in specs]
    assert wire[0]["regex"] == "[a-z]+"
    assert wire[1]["padrao"] == 0
    assert wire[4]["multilinha"] is True and wire[4]["segredo"] is True
    values = validate_values(specs, {"t": "abc", "m": "linha 1\nlinha 2\ttab"})
    assert values == {"t": "abc", "n": 0, "b": True, "e": None, "m": "linha 1\nlinha 2\ttab"}


@pytest.mark.parametrize(
    "schema, msg",
    [
        ("x", "precisa ser uma lista"),
        ([1], "dicionário"),
        ([{"tipo": "texto"}], "'nome'"),
        ([{"nome": "Maiusculo", "tipo": "texto"}], "'nome'"),
        ([{"nome": "a", "tipo": "texto"}, {"nome": "a", "tipo": "inteiro"}], "duas vezes"),
        ([{"nome": "a", "tipo": "arquivo"}], "'tipo'"),
        ([{"nome": "a", "tipo": "texto", "opcoes": ["x"]}], "chave desconhecida"),
        ([{"nome": "a", "tipo": "texto", "regex": "("}], "regex"),
        ([{"nome": "a", "tipo": "texto", "max": 99999}], "'max' de texto"),
        ([{"nome": "a", "tipo": "texto", "min": -1}], "negativo"),
        ([{"nome": "a", "tipo": "inteiro", "min": 5, "max": 1}], "maior que"),
        ([{"nome": "a", "tipo": "inteiro", "min": 1.5}], "inteiro"),
        ([{"nome": "a", "tipo": "inteiro", "padrao": 10, "max": 5}], "padrao"),
        ([{"nome": "a", "tipo": "escolha"}], "opcoes"),
        ([{"nome": "a", "tipo": "escolha", "opcoes": []}], "opcoes"),
        ([{"nome": "a", "tipo": "escolha", "opcoes": ["x", "x"]}], "opcoes"),
        ([{"nome": "a", "tipo": "escolha", "opcoes": ["x"], "padrao": "y"}], "padrao"),
        ([{"nome": "a", "tipo": "booleano", "obrigatorio": "sim"}], "True ou False"),
        ([{"nome": "a", "tipo": "texto", "rotulo": "x\ny"}], "rotulo"),
        ([{"nome": f"a{i}", "tipo": "texto"} for i in range(33)], "mais de 32"),
    ],
)
def test_bad_schemas(schema, msg):
    with pytest.raises(ValueError, match=msg):
        parse_schema(schema)


def test_required_without_default():
    specs = parse_schema([{"nome": "a", "tipo": "texto", "obrigatorio": True}])
    with pytest.raises(ParamError, match="obrigatório"):
        validate_values(specs, {})
    with pytest.raises(ParamError, match="obrigatório"):
        validate_values(specs, {"a": None})


def test_required_text_cannot_be_empty():
    specs = parse_schema([{"nome": "a", "tipo": "texto", "obrigatorio": True}])
    with pytest.raises(ParamError, match="vazio"):
        validate_values(specs, {"a": ""})
    optional = parse_schema([{"nome": "a", "tipo": "texto"}])
    assert validate_values(optional, {"a": ""}) == {"a": ""}


def test_text_default_limit():
    specs = parse_schema([{"nome": "a", "tipo": "texto"}])
    validate_values(specs, {"a": "x" * 4096})
    with pytest.raises(ParamError, match="4096"):
        validate_values(specs, {"a": "x" * 4097})


def test_int_limits_default_to_json_safe():
    specs = parse_schema([{"nome": "a", "tipo": "inteiro"}])
    validate_values(specs, {"a": 2**53})
    with pytest.raises(ParamError):
        validate_values(specs, {"a": 2**53 + 1})


@pytest.mark.parametrize(
    "kind, text, expected",
    [
        ("inteiro", " 42 ", 42),
        ("inteiro", "-3", -3),
        ("booleano", "sim", True),
        ("booleano", "Não", False),
        ("booleano", "1", True),
        ("texto", "  oi ", "  oi "),
    ],
)
def test_cli_values(kind, text, expected):
    assert parse_cli_value({"nome": "a", "tipo": kind}, text) == expected


@pytest.mark.parametrize("kind, text", [("inteiro", "4.5"), ("inteiro", "x"), ("booleano", "talvez")])
def test_bad_cli_values(kind, text):
    with pytest.raises(ParamError):
        parse_cli_value({"nome": "a", "tipo": kind}, text)


def test_regex_check_runs_out_of_process_with_timeout():
    import time

    from ponte.core.params import regex_fullmatch

    assert regex_fullmatch("[a-z]+", "abc") and not regex_fullmatch("[a-z]+", "ABC")
    t0 = time.monotonic()
    with pytest.raises(ParamError, match="demorou demais"):
        regex_fullmatch("(a+)+$", "a" * 40 + "!", timeout=0.5)
    assert time.monotonic() - t0 < 3
