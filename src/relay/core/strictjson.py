"""JSON estrito: sem NaN, sem Infinity e sem chave repetida."""

from __future__ import annotations

import json
import math


def _reject_constant(name: str):
    raise ValueError(f"constante {name} não é permitida")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("número fora do intervalo")
    return value


def loads_strict(raw: bytes | str) -> object:
    """json.loads sem NaN, sem Infinity e sem chaves repetidas."""

    def no_dupes(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                # Não repete o nome da chave: ele vem de fora e pode ser enorme.
                raise ValueError("chave repetida")
            out[k] = v
        return out

    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float, object_pairs_hook=no_dupes)
