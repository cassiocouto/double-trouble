"""Histórico local da interface, em SQLite (~/.local/share/ponte/historico.sqlite3)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import paths

KEEP_ROWS = 1000
MAX_TEXT = 512 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    maquina TEXT NOT NULL,
    comando TEXT NOT NULL,
    valores TEXT NOT NULL DEFAULT '{}',
    job TEXT,
    inicio REAL NOT NULL,
    duracao REAL,
    estado TEXT NOT NULL,
    codigo INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    truncado INTEGER NOT NULL DEFAULT 0,
    erro TEXT
);
CREATE INDEX IF NOT EXISTS execucoes_inicio ON execucoes (inicio DESC);
"""


@dataclass
class Entry:
    id: int
    machine: str
    command: str
    values: dict
    job: str | None
    started: float
    duration: float | None
    state: str
    code: int | None
    stdout: str
    stderr: str
    truncated: bool
    error: str | None


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.data_dir() / "historico.sqlite3")
        paths.ensure_private_dir(self.path.parent)
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        # Execução que ficou "rodando" é de uma janela que fechou antes do fim.
        self.db.execute("UPDATE execucoes SET estado = 'desconhecido' WHERE estado = 'rodando'")
        self.db.commit()

    def start(self, machine: str, command: str, values: dict) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO execucoes (maquina, comando, valores, inicio, estado) VALUES (?, ?, ?, ?, 'rodando')",
                (machine, command, json.dumps(values, ensure_ascii=False), time.time()),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def finish(
        self,
        row_id: int,
        *,
        job: str | None,
        state: str,
        code: int | None,
        duration: float | None,
        stdout: str,
        stderr: str,
        truncated: bool = False,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE execucoes SET job = ?, estado = ?, codigo = ?, duracao = ?, stdout = ?, stderr = ?,"
                " truncado = ?, erro = ? WHERE id = ?",
                (job, state, code, duration, stdout[:MAX_TEXT], stderr[:MAX_TEXT], int(truncated), error, row_id),
            )
            self.db.execute(
                "DELETE FROM execucoes WHERE id NOT IN (SELECT id FROM execucoes ORDER BY inicio DESC LIMIT ?)",
                (KEEP_ROWS,),
            )
            self.db.commit()

    def list(self, limit: int = 200) -> list[Entry]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, maquina, comando, valores, job, inicio, duracao, estado, codigo, stdout, stderr,"
                " truncado, erro FROM execucoes ORDER BY inicio DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._entry(r) for r in rows]

    def get(self, row_id: int) -> Entry | None:
        with self._lock:
            r = self.db.execute(
                "SELECT id, maquina, comando, valores, job, inicio, duracao, estado, codigo, stdout, stderr,"
                " truncado, erro FROM execucoes WHERE id = ?",
                (row_id,),
            ).fetchone()
        return self._entry(r) if r else None

    def clear(self) -> None:
        with self._lock:
            self.db.execute("DELETE FROM execucoes")
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @staticmethod
    def _entry(r) -> Entry:
        try:
            values = json.loads(r[3])
        except ValueError:
            values = {}
        return Entry(
            id=r[0],
            machine=r[1],
            command=r[2],
            values=values if isinstance(values, dict) else {},
            job=r[4],
            started=r[5],
            duration=r[6],
            state=r[7],
            code=r[8],
            stdout=r[9],
            stderr=r[10],
            truncated=bool(r[11]),
            error=r[12],
        )
