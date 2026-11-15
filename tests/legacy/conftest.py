"""Fixtures para testar o ponte.py v1 sem tocar no ~/.config/ponte real.

Cada teste ganha um PONTE_DIR temporario, um segredo novo e um servidor v1
rodando numa thread, escutando em 127.0.0.1 numa porta livre.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest

LEGACY = Path(__file__).resolve().parents[2] / "legacy" / "ponte.py"


def load_v1():
    """Carrega o ponte.py como modulo novo (BASE_DIR vem do PONTE_DIR atual)."""
    name = f"ponte_v1_{secrets.token_hex(4)}"
    spec = importlib.util.spec_from_file_location(name, LEGACY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def alive(pid: int) -> bool:
    """True se o processo existe e nao e zumbi."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in ("Z", "X")


def wait_until(pred, timeout: float = 5.0, step: float = 0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class V1:
    def __init__(self, mod, base: Path, key: bytes):
        self.mod = mod
        self.base = base
        self.key = key
        self.cmds = base / "comandos"
        self.srv = None
        self.port = 0
        self.thread = None

    # -- servidor --------------------------------------------------------
    def start(self, permitidos=None):
        cfg_path = self.base / "config.json"
        cfg = {
            "nome": "teste",
            "escutar": "127.0.0.1",
            "porta": 0,
            "segredo": self.key.hex(),
            "permitidos": permitidos or ["127.0.0.0/8"],
            "maquinas": {},
        }
        fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
        cfg = self.mod.load_config()
        self.srv = self.mod.PonteServer(cfg)
        self.port = self.srv.server_address[1]
        cfg["porta"] = self.port
        self.cfg = cfg
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()

    # -- comandos falsos -------------------------------------------------
    def make_cmd(self, name: str, code: str, mode: int = 0o600) -> Path:
        p = self.cmds / f"{name}.py"
        p.write_text(code, encoding="utf-8")
        os.chmod(p, mode)
        return p

    # -- rede ------------------------------------------------------------
    def raw(self, data: bytes, timeout: float = 15) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
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

    def build(self, body: dict, key: bytes | None = None, ts=None, nonce=None) -> dict:
        req = {
            "v": 1,
            "de": "testador",
            "ts": int(time.time()) if ts is None else ts,
            "nonce": nonce or secrets.token_hex(16),
            **body,
        }
        return self.mod.sign(key or self.key, "req", req)

    def send(self, req: dict, timeout: float = 15) -> dict | None:
        out = self.raw(json.dumps(req).encode() + b"\n", timeout=timeout)
        if not out:
            return None
        return json.loads(out.split(b"\n", 1)[0])

    def ask(self, body: dict, **kw) -> dict | None:
        return self.send(self.build(body, **kw))

    def signed(self, resp: dict) -> bool:
        return self.mod.verify(self.key, "resp", resp)


@pytest.fixture
def v1(tmp_path, monkeypatch):
    base = tmp_path / "ponte"
    base.mkdir(mode=0o700)
    (base / "comandos").mkdir(mode=0o700)
    monkeypatch.setenv("PONTE_DIR", str(base))
    mod = load_v1()
    h = V1(mod, base, secrets.token_bytes(32))
    yield h
    h.stop()
