"""Cliente do pacote falando com o ponte.py antigo, e o contrário (fase 1)."""

from __future__ import annotations

import json
import os
import secrets
import threading

import pytest

from .conftest import PackageAsV1, load_v1


@pytest.fixture
def pair(tmp_path, monkeypatch):
    base = tmp_path / "ponte"
    (base / "comandos").mkdir(parents=True, mode=0o700)
    os.chmod(base, 0o700)
    monkeypatch.setenv("PONTE_DIR", str(base))
    key = secrets.token_bytes(32)
    cfg = {
        "nome": "x",
        "escutar": "127.0.0.1",
        "porta": 0,
        "segredo": key.hex(),
        "permitidos": ["127.0.0.0/8"],
    }
    fd = os.open(base / "config.json", os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    p = base / "comandos" / "oi.py"
    p.write_text('"""Oi."""\nimport sys\nACEITA_ARGS = True\nprint("oi", *sys.argv[1:])\n')
    os.chmod(p, 0o600)
    return load_v1(), PackageAsV1()


def _serve(server_mod):
    cfg = server_mod.load_config()
    srv = server_mod.PonteServer(cfg)
    cfg["porta"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, cfg


@pytest.mark.parametrize("direction", ["pacote->legacy", "legacy->pacote"])
def test_interop(pair, direction):
    old, new = pair
    client, server = (new, old) if direction == "pacote->legacy" else (old, new)
    srv, cfg = _serve(server)
    try:
        _, resp = client.call(cfg, "local", {"acao": "rodar", "comando": "oi", "args": ["mundo"]}, 10)
        assert resp["stdout"] == "oi mundo\n"
        _, resp = client.call(cfg, "local", {"acao": "listar"}, 10)
        assert resp["comandos"][0]["descricao"] == "Oi."
    finally:
        srv.shutdown()
        srv.server_close()
