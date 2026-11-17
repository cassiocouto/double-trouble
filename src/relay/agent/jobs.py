"""Gerenciador de jobs: cada execução tem id, saída ao vivo, cancelamento e histórico."""

from __future__ import annotations

import secrets
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .execute import Artifact, read_artifact
from .runner import RunResult, RunSpec, run_process

KEEP_FINISHED = 50
MAX_CHUNK_CHARS = 32 * 1024
MAX_ARTIFACT_MEMORY = 64 * 1024 * 1024  # soma dos artefatos guardados na memória, entre todos os jobs

RUNNING = "rodando"
OK = "ok"
FAILED = "falhou"
CANCELLED = "cancelado"
TIMED_OUT = "tempo_esgotado"
ERROR = "erro"


class Busy(Exception):
    """Limite de jobs em paralelo atingido."""


def _cleanup_dir(path: str | None) -> None:
    if not path:
        return
    import shutil

    shutil.rmtree(path, ignore_errors=True)


@dataclass
class Job:
    id: str
    controller: str
    command: str
    params: dict | None
    args: list[str]
    timeout: float
    started_at: float = field(default_factory=time.time)
    state: str = RUNNING
    result: RunResult | None = None
    finished_at: float | None = None
    chunks: list[tuple[str, str]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cond: threading.Condition = field(default_factory=threading.Condition)
    _sealed: int = 0
    artifact: Artifact | None = None  # imagem/PDF etc. que o comando escreveu
    artifact_error: str | None = None  # motivo de não ter artefato (grande demais, link...)

    # -- saída ------------------------------------------------------------
    def append(self, stream: str, text: str) -> None:
        with self.cond:
            last = len(self.chunks) - 1
            if last >= self._sealed and self.chunks[last][0] == stream:
                # Junta com o último pedaço se ninguém viu ele ainda (menos frames).
                prev = self.chunks[last][1]
                room = MAX_CHUNK_CHARS - len(prev)
                if room > 0:
                    self.chunks[last] = (stream, prev + text[:room])
                    text = text[room:]
            while text:
                self.chunks.append((stream, text[:MAX_CHUNK_CHARS]))
                text = text[MAX_CHUNK_CHARS:]
            self.cond.notify_all()

    def wait_new(self, index: int, timeout: float) -> tuple[list[tuple[str, str]], int, bool]:
        """Espera pedaços novos a partir de `index`. Devolve (pedaços, novo índice, terminou).

        Pedaço entregue fica selado e nunca mais muda, então quem acompanha
        não perde nada.
        """
        with self.cond:
            if len(self.chunks) <= index and self.state == RUNNING:
                self.cond.wait(timeout)
            new = list(self.chunks[index:])
            self._sealed = len(self.chunks)
            return new, len(self.chunks), self.state != RUNNING

    def output(self, stream: str) -> str:
        with self.cond:
            return "".join(t for s, t in self.chunks if s == stream)

    # -- estado -----------------------------------------------------------
    @property
    def done(self) -> bool:
        return self.state != RUNNING

    def duration(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 3)

    def summary(self) -> dict:
        r = self.result
        return {
            "job": self.id,
            "comando": self.command,
            "estado": self.state,
            "inicio": self.started_at,
            "duracao": self.duration(),
            "codigo": r.codigo if r else None,
            "truncado": bool(r and r.truncado),
            "timeout": self.timeout,
        }

    def result_dict(self) -> dict:
        out = self.summary()
        r = self.result
        out["ok"] = self.state == OK
        out["estourou_tempo"] = self.state == TIMED_OUT
        out["cancelado"] = self.state == CANCELLED
        if r and r.erro:
            out["erro"] = r.erro
        if self.artifact is not None:
            out["artefato"] = {
                "tipo": self.artifact.kind.tipo,
                "imagem": self.artifact.kind.imagem,
                "bytes": len(self.artifact.data),
            }
        if self.artifact_error:
            out["artefato_erro"] = self.artifact_error
        return out


class JobManager:
    def __init__(
        self,
        max_parallel: int = 4,
        keep: int = KEEP_FINISHED,
        on_finish: Callable[[Job], None] | None = None,
    ):
        self.max_parallel = max_parallel
        self.on_finish = on_finish
        self._lock = threading.Lock()
        self._running: dict[str, Job] = {}
        # Um histórico por controlador: um não empurra os resultados do outro pra fora.
        self.keep = keep
        self._finished: dict[str, deque[Job]] = {}
        self._threads: dict[str, threading.Thread] = {}

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    def start(
        self,
        controller: str,
        command: str,
        spec: RunSpec,
        params: dict | None,
        args: list[str],
        on_spawned: Callable[[], None] | None = None,
        artifact_file: str | None = None,
        artifact_dir: str | None = None,
    ) -> Job:
        """Começa o job numa thread própria. Levanta Busy se estiver no limite.

        artifact_file é onde o comando pode escrever uma imagem/PDF que volta
        pro controlador. artifact_dir é a pasta temporária a apagar no fim.
        """
        job = Job(
            id=secrets.token_hex(8),
            controller=controller,
            command=command,
            params=params,
            args=list(args),
            timeout=spec.timeout,
        )
        with self._lock:
            if len(self._running) >= self.max_parallel:
                raise Busy(f"já tem {self.max_parallel} comandos rodando, tente de novo daqui a pouco")
            self._running[job.id] = job
        spawned = threading.Event()

        def started(_pid: int) -> None:
            spawned.set()

        def work() -> None:
            try:
                result = run_process(spec, job.append, job.cancel_event, on_start=started)
            except Exception as e:  # nunca deixar um job derrubar o agente
                result = RunResult(codigo=None, erro=f"erro interno rodando o comando ({e!r})")
            finally:
                spawned.set()
            # Lê o arquivo de saída se o comando rodou até o fim (mesmo saindo com
            # código != 0). Em timeout ou cancelamento o arquivo pode estar pela
            # metade, então nem olha.
            if artifact_file and not result.erro and not result.estourou_tempo and not result.cancelado:
                self._read_artifact(job, artifact_file)
            _cleanup_dir(artifact_dir)
            self._finish(job, result)

        t = threading.Thread(target=work, name=f"job-{job.id}", daemon=True)
        with self._lock:
            self._threads[job.id] = t
        t.start()
        # Espera o processo existir (ou falhar) antes de devolver. Assim quem
        # chamou pode fechar o fd do comando com segurança.
        spawned.wait(60)
        if on_spawned:
            on_spawned()
        return job

    def _read_artifact(self, job: Job, path: str) -> None:
        try:
            job.artifact = read_artifact(path)
        except ValueError as e:
            job.artifact_error = str(e)
        except Exception as e:  # nada no artefato pode derrubar o job
            job.artifact_error = f"não consegui ler o arquivo de saída ({e!r})"
        if job.artifact is not None:
            self._bound_artifact_memory(job)

    def _bound_artifact_memory(self, new: Job) -> None:
        """Mantém a soma dos artefatos guardados abaixo do teto, soltando os mais velhos."""
        with self._lock:
            jobs = [new, *(j for d in self._finished.values() for j in d if j.artifact is not None and j is not new)]
            total = 0
            for job in jobs:
                if job.artifact is None:
                    continue
                total += len(job.artifact.data)
                if total > MAX_ARTIFACT_MEMORY and job is not new:
                    job.artifact = None
                    job.artifact_error = "a imagem saiu da memória do agente (rode de novo)"

    def _finish(self, job: Job, result: RunResult) -> None:
        if result.erro:
            state = ERROR
        elif result.cancelado:
            state = CANCELLED
        elif result.estourou_tempo:
            state = TIMED_OUT
        elif result.codigo == 0:
            state = OK
        else:
            state = FAILED
        with job.cond:
            job.result = result
            job.finished_at = time.time()
            job.state = state
            job.cond.notify_all()
        with self._lock:
            self._running.pop(job.id, None)
            self._threads.pop(job.id, None)
            self._finished.setdefault(job.controller, deque(maxlen=self.keep)).append(job)
        if self.on_finish:
            try:
                self.on_finish(job)
            except Exception as e:  # auditoria quebrada não derruba nada
                print(f"falha no aviso de fim do job {job.id} ({e!r})", file=sys.stderr, flush=True)

    def get(self, job_id: object, controller: str) -> Job | None:
        """Job do próprio controlador. Job de outro é tratado como inexistente."""
        if not isinstance(job_id, str):
            return None
        with self._lock:
            job = self._running.get(job_id) or next(
                (j for j in self._finished.get(controller, ()) if j.id == job_id), None
            )
        if job is None or job.controller != controller:
            return None
        return job

    def cancel(self, job_id: object, controller: str) -> Job | None:
        job = self.get(job_id, controller)
        if job is not None and not job.done:
            job.cancel_event.set()
        return job

    def recent(self, controller: str, limit: int = KEEP_FINISHED) -> list[Job]:
        with self._lock:
            jobs = [*self._running.values(), *self._finished.get(controller, ())]
        mine = [j for j in jobs if j.controller == controller]
        mine.sort(key=lambda j: j.started_at, reverse=True)
        return mine[:limit]

    def shutdown(self, timeout: float = 10) -> None:
        """Cancela tudo e espera os grupos morrerem."""
        with self._lock:
            running = list(self._running.values())
            threads = list(self._threads.values())
        for job in running:
            job.cancel_event.set()
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
