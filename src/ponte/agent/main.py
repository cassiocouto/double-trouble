"""ponte-agent da fase 1: serve o protocolo v1 com o config.json antigo."""

from __future__ import annotations

import signal
import sys

from ..core import legacy, paths
from ..core.errors import PonteError
from .legacy_server import LegacyServer, log


def main() -> None:
    try:
        cfg = legacy.load_v1_config(paths.legacy_config_file())
        srv = LegacyServer(cfg)
    except (PonteError, OSError) as e:
        print(f"ponte-agent: {e}", file=sys.stderr)
        sys.exit(1)
    log(f"ponte {cfg['nome']} escutando em {cfg['escutar']}:{srv.server_address[1]}")

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("desligando")
    finally:
        srv.server_close()
