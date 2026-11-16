"""relay-agent: o processo que o systemd do usuário mantém rodando."""

from __future__ import annotations

import argparse
import signal
import sys

from .. import __version__
from ..core.config import ConfigSource
from ..core.errors import RelayError
from .audit import AuditLog
from .server import log
from .service import AgentService, check_not_root


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="relay-agent", description="Agente do relay (o lado que executa).")
    p.add_argument("--versao", action="version", version=f"relay-agent {__version__}")
    p.parse_args(argv)
    try:
        check_not_root()
        source = ConfigSource(on_error=log)
    except RelayError as e:
        print(f"relay-agent: {e}", file=sys.stderr)
        sys.exit(1)
    service = AgentService(source, audit=AuditLog())

    def stop(*_):
        service.stop()

    def reload(*_):
        source._stamp = None  # força reler o config na próxima vez
        log("SIGHUP: relendo o config")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, reload)
    service.run()
