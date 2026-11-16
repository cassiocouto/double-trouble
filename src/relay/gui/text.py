"""Textos e números do jeito brasileiro, pra mostrar na tela."""

from __future__ import annotations

import time
import unicodedata

STATE_LABELS = {
    "rodando": "Rodando",
    "ok": "Deu certo",
    "falhou": "Falhou",
    "cancelado": "Cancelado",
    "tempo_esgotado": "Tempo esgotado",
    "erro": "Não iniciou",
    "desconhecido": "Sem resultado",
}


def number(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{number(seconds, 2)} s"
    if seconds < 60:
        return f"{number(seconds)} s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def skew(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "−"
    return f"relógio {sign}{number(abs(seconds))} s"


def when(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return ""
    now = time.time() if now is None else now
    lt = time.localtime(ts)
    if time.strftime("%Y-%m-%d", lt) == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "hoje às " + time.strftime("%H:%M", lt)
    return time.strftime("%d/%m %H:%M", lt)


def state(name: str | None) -> str:
    return STATE_LABELS.get(name or "", name or "")


def fold(text: str) -> str:
    """Minúsculo e sem acento, pra busca."""
    norm = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in norm if not unicodedata.combining(c))
