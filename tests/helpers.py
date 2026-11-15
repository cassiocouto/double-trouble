"""Peças pra subir agentes v2 de verdade em loopback, cada um na sua pasta."""

from __future__ import annotations

import contextlib
import os
import secrets
import socket
import struct
import threading
from pathlib import Path

from ponte.agent.jobs import JobManager
from ponte.agent.server import AgentServer
from ponte.core import config as config_mod
from ponte.core import protocol
from ponte.core.client import Client
from ponte.core.config import Config, ConfigSource, ControllerEntry, MachineEntry


class AgentHarness:
    """Um agente rodando numa thread, com config e comandos numa pasta própria."""

    def __init__(self, root: Path, name: str = "agente", controller: str = "controle", **agent_opts):
        self.root = root
        self.name = name
        self.controller = controller
        self.base = root / "ponte"
        self.base.mkdir(parents=True, mode=0o700)
        os.chmod(self.base, 0o700)
        (self.base / "comandos").mkdir(mode=0o700)
        self.config_path = self.base / "ponte.json"
        self.secret = secrets.token_bytes(32)
        cfg = Config(name=name)
        cfg.agent.listen = "127.0.0.1"
        cfg.agent.port = 0
        cfg.agent.allowed = agent_opts.pop("allowed", ["127.0.0.0/8"])
        cfg.agent.verify_tailscale = agent_opts.pop("verify_tailscale", "nunca")
        cfg.agent.max_parallel = agent_opts.pop("max_parallel", 4)
        cfg.agent.controllers[controller] = ControllerEntry(
            name=controller, secret=self.secret, commands=agent_opts.pop("commands", None)
        )
        config_mod.save(cfg, self.config_path)
        self.audit: list[dict] = []
        self.source = ConfigSource(self.config_path)
        self.srv = AgentServer(
            self.source,
            "127.0.0.1",
            0,
            jobs=JobManager(max_parallel=cfg.agent.max_parallel),
            audit=self.audit.append,
            base_dir=self.base,
            **agent_opts,
        )
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(
            target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.srv.jobs.shutdown(timeout=10)

    # -- comandos ---------------------------------------------------------
    def make_cmd(self, name: str, code: str, mode: int = 0o600) -> Path:
        p = self.base / "comandos" / f"{name}.py"
        p.write_text(code, encoding="utf-8")
        os.chmod(p, mode)
        return p

    def update(self, change) -> None:
        config_mod.update(change, self.config_path)

    # -- clientes ---------------------------------------------------------
    def controller_config(
        self, name: str | None = None, secret: bytes | None = None, agent: str | None = None
    ):
        cfg = Config(name=name or self.controller)
        target = agent or self.name
        cfg.machines[target] = MachineEntry(
            name=target, address="127.0.0.1", port=self.port, secret=secret or self.secret
        )
        return cfg, target

    def client(self, name: str | None = None, secret: bytes | None = None, agent: str | None = None, **kw):
        cfg, target = self.controller_config(name, secret, agent)
        return Client(cfg, cfg.machines[target], **kw)

    def key(self, controller: str | None = None) -> bytes:
        return protocol.derive_key(self.secret, controller or self.controller, self.name)

    # -- conversa crua ----------------------------------------------------
    def raw(self, data: bytes, timeout: float = 10) -> bytes:
        """Manda bytes crus e devolve tudo que voltar até o agente fechar."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            with contextlib.suppress(OSError):
                s.sendall(data)
            out = bytearray()
            while True:
                try:
                    chunk = s.recv(65536)
                except (ConnectionResetError, TimeoutError):
                    break
                if not chunk:
                    break
                out += chunk
        return bytes(out)

    def raw_frames(self, req: dict | bytes, timeout: float = 15) -> list[dict]:
        """Manda um pedido (já montado) e devolve os frames de resposta, sem conferir nada."""
        payload = req if isinstance(req, bytes) else protocol.encode_frame(req)
        data = self.raw(protocol.MAGIC + payload, timeout=timeout)
        assert data.startswith(protocol.MAGIC), data[:40]
        return parse_frames(data[len(protocol.MAGIC) :])

    def build(self, action: str, key: bytes | None = None, controller: str | None = None, **fields) -> dict:
        return protocol.build_request(
            key or self.key(controller), controller or self.controller, self.name, action, **fields
        )


def parse_frames(data: bytes) -> list[dict]:
    import json

    frames = []
    while data:
        (size,) = struct.unpack(">I", data[:4])
        frames.append(json.loads(data[4 : 4 + size]))
        data = data[4 + size :]
    return frames


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
