"""Sinal simples (observer) pra os view-models avisarem a tela, sem depender do GObject."""

from __future__ import annotations

from collections.abc import Callable


class Signal:
    def __init__(self) -> None:
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> Callable:
        self._handlers.append(handler)
        return handler

    def disconnect(self, handler: Callable) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def emit(self, *args) -> None:
        for handler in list(self._handlers):
            handler(*args)
