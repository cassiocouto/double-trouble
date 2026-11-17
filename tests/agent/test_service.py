"""Serviço: sd_notify, watchdog, espera do Tailscale, rebind e auditoria."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from relay.agent import systemd
from relay.agent.audit import AuditLog
from relay.agent.service import AgentService
from relay.core import config as config_mod
from relay.core.config import ConfigSource, ControllerEntry

from ..conftest import wait_until
from ..helpers import free_port

SRC = Path(__file__).resolve().parents[2] / "src"


class NotifySocket:
    def __init__(self, path: Path, abstract: bool = False):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if abstract:
            self.name = f"@relay-teste-{os.getpid()}-{time.monotonic_ns()}"
            self.sock.bind("\0" + self.name[1:])
        else:
            self.name = str(path)
            self.sock.bind(self.name)
        self.sock.settimeout(0.2)
        self.messages: list[str] = []
        self._stop = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self._stop:
            try:
                self.messages.append(self.sock.recv(4096).decode())
            except TimeoutError:
                continue
            except OSError:
                return

    def has(self, text: str) -> bool:
        return any(text in m for m in self.messages)

    def close(self):
        self._stop = True
        self.sock.close()


@pytest.mark.parametrize("abstract", [False, True])
def test_sd_notify(tmp_path, monkeypatch, abstract):
    ns = NotifySocket(tmp_path / "notify", abstract)
    monkeypatch.setenv("NOTIFY_SOCKET", ns.name)
    assert systemd.notify("READY=1", "STATUS=oi")
    assert wait_until(lambda: ns.has("READY=1\nSTATUS=oi"))
    ns.close()


def test_sd_notify_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert systemd.notify("READY=1") is False
    monkeypatch.setenv("NOTIFY_SOCKET", "vsock:1:2")
    assert systemd.notify("READY=1") is False
    monkeypatch.setenv("NOTIFY_SOCKET", "/nao/existe")
    assert systemd.notify("READY=1") is False


def test_watchdog_interval(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    assert systemd.watchdog_interval() == 30
    monkeypatch.setenv("WATCHDOG_PID", "1")
    assert systemd.watchdog_interval() is None
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    assert systemd.watchdog_interval() == 30
    monkeypatch.setenv("WATCHDOG_USEC", "lixo")
    assert systemd.watchdog_interval() is None


# --------------------------------------------------------------------------
# auditoria


def test_audit_writes_private_jsonl(tmp_path):
    log = AuditLog(tmp_path / "estado")
    log({"evento": "rodar", "de": "x", "parametros": {"a": "ç"}})
    assert oct(os.stat(log.path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(log.dir).st_mode & 0o777) == "0o700"
    [rec] = log.read()
    assert rec["evento"] == "rodar" and rec["parametros"] == {"a": "ç"} and "ts" in rec


def test_audit_rotation_caps_disk(tmp_path):
    log = AuditLog(tmp_path / "estado", max_bytes=2000, keep=3)
    for i in range(500):
        log({"evento": "x", "i": i, "pad": "y" * 50})
    files = sorted(p.name for p in log.dir.iterdir())
    assert files == ["auditoria.jsonl", "auditoria.jsonl.1", "auditoria.jsonl.2", "auditoria.jsonl.3"]
    total = sum(p.stat().st_size for p in log.dir.iterdir())
    assert total <= 4 * 2000
    assert log.read()[-1]["i"] == 499


def test_audit_refuses_open_dir(tmp_path, capsys):
    d = tmp_path / "estado"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o777)
    log = AuditLog(d)
    log({"evento": "x"})
    assert not log.path.exists()
    assert "auditoria desligada" in capsys.readouterr().err


# --------------------------------------------------------------------------
# ciclo do serviço


def make_config(tmp_path, port=0):
    base = tmp_path / "relay"
    (base / "comandos").mkdir(parents=True, mode=0o700)
    os.chmod(base, 0o700)
    cfg = config_mod.Config(name="agente")
    cfg.agent.listen = "tailscale"
    cfg.agent.port = port
    cfg.agent.allowed = ["127.0.0.0/8"]
    cfg.agent.verify_tailscale = "nunca"
    secret = os.urandom(32)
    cfg.agent.controllers["controle"] = ControllerEntry("controle", secret)
    config_mod.save(cfg, base / "relay.json")
    return base, secret


def test_service_waits_for_tailscale_then_listens(tmp_path):
    base, _ = make_config(tmp_path)
    calls = {"n": 0}

    def find_ip():
        calls["n"] += 1
        return "127.0.0.1" if calls["n"] >= 3 else None

    sent = []
    svc = AgentService(
        ConfigSource(base / "relay.json"),
        base_dir=base,
        notify=lambda *m: sent.append(m) or True,
        find_ip=find_ip,
        max_backoff=0.2,
        ready_grace=60,
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    assert wait_until(lambda: svc.server is not None, timeout=10)
    statuses = [m for msg in sent for m in msg if m.startswith("STATUS=")]
    assert any("esperando o Tailscale" in s for s in statuses)
    ready = [msg for msg in sent if "READY=1" in msg]
    assert len(ready) == 1
    # READY só depois de escutar (não durante a espera)
    assert any("escutando" in m for m in ready[0])
    svc.stop()
    t.join(10)
    assert ("STOPPING=1",) in sent


def test_service_sends_ready_after_grace_without_tailscale(tmp_path):
    base, _ = make_config(tmp_path)
    sent = []
    svc = AgentService(
        ConfigSource(base / "relay.json"),
        base_dir=base,
        notify=lambda *m: sent.append(m) or True,
        find_ip=lambda: None,
        max_backoff=0.1,
        ready_grace=0.3,
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    assert wait_until(lambda: any("READY=1" in m for m in sent), timeout=5)
    assert svc.server is None  # continua esperando, sem sair com erro
    assert t.is_alive()
    svc.stop()
    t.join(10)


def test_service_rebinds_when_ip_changes(tmp_path):
    base, _secret = make_config(tmp_path, port=free_port())
    ip = {"v": "127.0.0.1"}
    svc = AgentService(
        ConfigSource(base / "relay.json"),
        base_dir=base,
        notify=lambda *m: True,
        find_ip=lambda: ip["v"],
        check_every=0.2,
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    assert wait_until(lambda: svc.address and svc.address[0] == "127.0.0.1", timeout=10)
    ip["v"] = "127.0.0.2"
    assert wait_until(lambda: svc.address and svc.address[0] == "127.0.0.2", timeout=10)
    svc.stop()
    t.join(10)


def test_service_survives_broken_config(tmp_path):
    base, _ = make_config(tmp_path)
    sent = []
    source = ConfigSource(base / "relay.json")
    (base / "relay.json").write_text("{quebrado")
    svc = AgentService(
        source, base_dir=base, notify=lambda *m: sent.append(m) or True, find_ip=lambda: "127.0.0.1", ready_grace=0
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    assert wait_until(lambda: any("config com problema" in x for m in sent for x in m), timeout=5)
    assert svc.server is None
    svc.stop()
    t.join(15)
    assert not t.is_alive()


def test_service_audits_job_end(tmp_path):
    from relay.core.client import Client
    from relay.core.config import Config, MachineEntry

    base, secret = make_config(tmp_path)
    p = base / "comandos" / "oi.py"
    p.write_text('import sys\nprint("oi")\nsys.exit(3)\n')
    os.chmod(p, 0o600)
    audit = AuditLog(tmp_path / "estado")
    svc = AgentService(
        ConfigSource(base / "relay.json"),
        audit=audit,
        base_dir=base,
        notify=lambda *m: True,
        find_ip=lambda: "127.0.0.1",
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    assert wait_until(lambda: svc.address is not None, timeout=10)
    cfg = Config(name="controle")
    m = MachineEntry("agente", "127.0.0.1", svc.address[1], secret)
    out = Client(cfg, m).run("oi")
    assert out.result["codigo"] == 3
    assert wait_until(lambda: any(r["evento"] == "fim" for r in audit.read()))
    records = {r["evento"]: r for r in audit.read()}
    assert records["rodar"]["de"] == "controle" and records["rodar"]["comando"] == "oi"
    fim = records["fim"]
    assert fim["codigo"] == 3 and fim["estado"] == "falhou" and fim["duracao"] >= 0
    svc.stop()
    t.join(10)


# --------------------------------------------------------------------------
# o executável de verdade, com NOTIFY_SOCKET e watchdog


def test_agent_process_notifies_systemd(tmp_path):
    base, _ = make_config(tmp_path, port=free_port())

    def loopback(cfg):
        cfg.agent.listen = "127.0.0.1"

    config_mod.update(loopback, base / "relay.json")
    ns = NotifySocket(tmp_path / "notify")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "RELAY_DIR": str(base),
        "RELAY_STATE_DIR": str(tmp_path / "estado"),
        "PYTHONPATH": str(SRC),
        "NOTIFY_SOCKET": ns.name,
        "WATCHDOG_USEC": "1000000",
    }
    proc = subprocess.Popen([sys.executable, "-m", "relay.agent"], env=env, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_until(lambda: ns.has("READY=1"), timeout=10)
        assert wait_until(lambda: ns.has("WATCHDOG=1"), timeout=5)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(15) == 0
        assert ns.has("STOPPING=1")
    finally:
        if proc.poll() is None:
            proc.kill()
        ns.close()


def test_agent_process_refuses_root(tmp_path, monkeypatch):
    from relay.agent import service
    from relay.core.errors import RelayError

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(RelayError, match="não roda como root"):
        service.check_not_root()


def test_service_reports_busy_port_and_still_becomes_ready(tmp_path):
    """Porta ocupada por outro programa: explica no STATUS, avisa o systemd e segue tentando."""
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    base, _ = make_config(tmp_path, port=port)
    sent = []
    svc = AgentService(
        ConfigSource(base / "relay.json"),
        base_dir=base,
        notify=lambda *m: sent.append(m) or True,
        find_ip=lambda: "127.0.0.1",
        max_backoff=0.1,
        ready_grace=0.3,
    )
    t = threading.Thread(target=svc.run, daemon=True)
    t.start()
    try:
        assert wait_until(lambda: any("READY=1" in m for m in sent), timeout=5)
        statuses = [x for m in sent for x in m if x.startswith("STATUS=")]
        assert any("Outro programa já usa a porta" in x and "ss -ltnp" in x for x in statuses)
        assert svc.server is None
        busy.close()  # a porta liberou: o agente pega ela sozinho
        assert wait_until(lambda: svc.server is not None, timeout=5)
    finally:
        busy.close()
        svc.stop()
        t.join(10)
