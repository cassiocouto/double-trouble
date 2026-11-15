"""ponte-agent: o processo que fica escutando pedidos e roda os comandos."""

from __future__ import annotations

import argparse
import signal
import sys

from ..core import config as config_mod
from ..core import tailscale
from ..core.errors import PonteError
from .server import AgentServer, log


def resolve_listen(cfg: config_mod.Config) -> str | None:
    if cfg.agent.listen != "tailscale":
        return cfg.agent.listen
    return tailscale.own_ip(4)


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(prog="ponte-agent", description="Agente do ponte (lado que executa).").parse_args(
        argv
    )
    try:
        source = config_mod.ConfigSource(on_error=log)
        cfg = source.get()
        assert cfg is not None
        host = resolve_listen(cfg)
        if not host:
            raise PonteError("não achei o IP do Tailscale (tailscale ip -4)")
        srv = AgentServer(source, host, cfg.agent.port)
    except (PonteError, OSError) as e:
        print(f"ponte-agent: {e}", file=sys.stderr)
        sys.exit(1)
    log(f"ponte {cfg.name} escutando em {host}:{srv.server_address[1]}")

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("desligando")
    finally:
        srv.server_close()
        srv.jobs.shutdown()
