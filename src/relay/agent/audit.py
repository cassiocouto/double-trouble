"""Log de auditoria em JSON lines, em ~/.local/state/relay/auditoria.jsonl.

Cada linha é um objeto com "ts" (ISO 8601) e "evento". Quando o arquivo
passa de MAX_BYTES, ele vira auditoria.jsonl.1, o .1 vira .2, e assim até
KEEP. O mais velho é apagado. Então o disco usado tem teto.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from ..core import paths
from ..core.safety import describe_unsafe

MAX_BYTES = 1024 * 1024
KEEP = 5
FILE_NAME = "auditoria.jsonl"


class AuditLog:
    def __init__(self, directory: Path | None = None, max_bytes: int = MAX_BYTES, keep: int = KEEP):
        self.dir = directory or paths.state_dir()
        self.max_bytes = max_bytes
        self.keep = keep
        self.path = self.dir / FILE_NAME
        self._lock = threading.Lock()
        self._warned = False

    def _ready(self) -> bool:
        paths.ensure_private_dir(self.dir)
        reason = describe_unsafe(os.stat(self.dir), private=True)
        if reason:
            if not self._warned:
                print(f"auditoria desligada: a pasta {self.dir} {reason}", file=sys.stderr, flush=True)
                self._warned = True
            return False
        return True

    def _rotate(self) -> None:
        oldest = self.dir / f"{FILE_NAME}.{self.keep}"
        if oldest.exists():
            oldest.unlink()
        for i in range(self.keep - 1, 0, -1):
            src = self.dir / f"{FILE_NAME}.{i}"
            if src.exists():
                os.replace(src, self.dir / f"{FILE_NAME}.{i + 1}")
        if self.path.exists():
            os.replace(self.path, self.dir / f"{FILE_NAME}.1")

    def write(self, event: dict) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
        line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with self._lock:
            if not self._ready():
                return
            try:
                size = os.path.getsize(self.path)
            except FileNotFoundError:
                size = 0
            if size and size + len(line) > self.max_bytes:
                self._rotate()
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)

    __call__ = write

    def read(self) -> list[dict]:
        """Lê o arquivo atual (pra testes e pra depurar)."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in text.splitlines() if line.strip()]
