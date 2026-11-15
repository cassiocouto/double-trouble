"""Jobs, saída ao vivo, cancelamento, histórico, permissões e whois."""

from __future__ import annotations

import os
import threading
import time

import pytest

from ponte.core import protocol
from ponte.core.config import ControllerEntry
from ponte.core.errors import AuthError, RemoteError

from ..conftest import wait_until

TICKER = """
import sys, time
TIMEOUT = 30
for i in range(5):
    print(f"linha {i}", flush=True)
    print(f"erro {i}", file=sys.stderr, flush=True)
    time.sleep(0.2)
"""


def test_stream_arrives_live_and_in_order(agent):
    h = agent()
    h.make_cmd("ticker", TICKER)
    times = []
    out = h.client().run(
        "ticker", on_event=lambda ev: times.append(time.monotonic()) if ev["tipo"] == "saida" else None
    )
    assert out.text_stdout == "".join(f"linha {i}\n" for i in range(5))
    assert out.text_stderr == "".join(f"erro {i}\n" for i in range(5))
    assert len(times) >= 3, "a saída precisa chegar aos poucos, não tudo no fim"
    assert times[-1] - times[0] > 0.4
    assert out.result["estado"] == "ok"
    assert out.result["codigo"] == 0


def test_seq_numbers_and_types(agent):
    h = agent()
    h.make_cmd("ticker", TICKER)
    req = h.build("rodar", comando="ticker")
    frames = h.raw_frames(req)
    assert [f["seq"] for f in frames] == list(range(len(frames)))
    assert frames[0]["tipo"] == "aceito"
    assert frames[-1]["tipo"] == "fim" and frames[-1]["fim"] is True
    assert all(not f["fim"] for f in frames[:-1])
    assert all(protocol.verify(h.key(), protocol.KIND_RESPONSE, f) for f in frames)


def test_heartbeat_when_silent(agent, monkeypatch):
    from ponte.agent import server

    monkeypatch.setattr(server, "HEARTBEAT", 0.3)
    h = agent()
    h.make_cmd("quieto", "import time\ntime.sleep(1.2)\n")
    tipos = []
    h.client().run("quieto", on_event=lambda ev: tipos.append(ev["tipo"]))
    assert "vivo" in tipos


def test_exit_code_and_failure(agent):
    h = agent()
    h.make_cmd("falha", "import sys\nprint('ops', file=sys.stderr)\nsys.exit(5)\n")
    out = h.client().run("falha")
    assert out.result["estado"] == "falhou"
    assert out.result["codigo"] == 5
    assert out.text_stderr == "ops\n"


def test_detached_run_and_status(agent):
    h = agent()
    h.make_cmd("lento", "import time\nprint('a', flush=True)\ntime.sleep(1)\nprint('b')\n")
    c = h.client()
    out = c.run("lento", follow=False)
    job = out.job
    assert job
    snap = h.client().status(job)
    assert snap.result["estado"] in ("rodando", "ok")
    follow = h.client().status(job, follow=True)
    assert follow.text_stdout == "a\nb\n"
    assert follow.result["estado"] == "ok"
    again = h.client().status(job)
    assert again.text_stdout == "a\nb\n"


def test_parallel_limit(agent):
    h = agent(max_parallel=2)
    h.make_cmd("lento", "import time\ntime.sleep(2)\n")
    started = [h.client().run("lento", follow=False) for _ in range(2)]
    with pytest.raises(RemoteError, match="já tem 2 comandos rodando"):
        h.client().run("lento")
    for s in started:
        h.client().status(s.job, follow=True)
    assert h.client().run("lento", follow=False).job


def test_history_is_per_controller(agent):
    h = agent()
    other_secret = os.urandom(32)

    def add(cfg):
        cfg.agent.controllers["outro"] = ControllerEntry(name="outro", secret=other_secret)

    h.update(add)
    h.make_cmd("oi", "print('oi')\n")
    mine = h.client().run("oi")
    theirs = h.client(name="outro", secret=other_secret).run("oi")
    hist = h.client().history()
    assert [j["job"] for j in hist] == [mine.job]
    # Job de outro controlador não existe pra mim: nem status, nem cancelar.
    with pytest.raises(RemoteError, match="não achei"):
        h.client().status(theirs.job)
    with pytest.raises(RemoteError, match="não achei"):
        h.client().cancel(theirs.job)


def test_cancel_unknown_job(agent):
    h = agent()
    for bad in ("nao-existe", "", 5, None, "x" * 100):
        req = h.build("cancelar", job=bad)
        assert h.raw_frames(req)[0]["codigo_erro"] == "job"


def test_per_controller_command_permissions(agent):
    h = agent(commands=["permitido"])
    h.make_cmd("permitido", "print('sim')\n")
    h.make_cmd("proibido", "print('nao')\n")
    c = h.client()
    assert [x["nome"] for x in c.list_commands()] == ["permitido"]
    assert c.run("permitido").text_stdout == "sim\n"
    with pytest.raises(RemoteError, match="não deixa"):
        h.client().run("proibido")
    assert any(e["evento"] == "sem_permissao" for e in h.audit)


def test_revoked_controller_loses_access(agent):
    h = agent()
    assert h.client().ping()

    def revoke(cfg):
        del cfg.agent.controllers["controle"]

    h.update(revoke)
    with pytest.raises(AuthError):
        h.client().ping()


def test_ping_fields(agent):
    h = agent()
    p = h.client().ping()
    assert p.machine == "agente"
    assert p.protocol == 2
    assert abs(p.clock_skew) < 2
    assert p.latency_ms >= 0


def test_listing_metadata(agent):
    h = agent()
    h.make_cmd(
        "completo",
        '"""Resumo.\n\nDetalhes aqui."""\nICONE = "computer-symbolic"\nCATEGORIA = "Sistema"\n'
        'CONFIRMAR = True\nTIMEOUT = 5\nPARAMETROS = [{"nome": "x", "tipo": "inteiro", "padrao": 2}]\n',
    )
    [c] = h.client().list_commands()
    assert c["descricao"] == "Resumo."
    assert c["detalhes"] == "Detalhes aqui."
    assert c["icone"] == "computer-symbolic"
    assert c["categoria"] == "Sistema"
    assert c["confirmar"] is True
    assert c["timeout"] == 5
    assert c["parametros"] == [{"nome": "x", "tipo": "inteiro", "rotulo": "x", "obrigatorio": False, "padrao": 2}]


def test_concurrent_streams(agent):
    h = agent()
    h.make_cmd("ticker", TICKER)
    results = []

    def go():
        results.append(h.client().run("ticker").text_stdout)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3 and len(set(results)) == 1


# ==========================================================================
# tailscale whois (camada extra)


def fake_tailscale(tmp_path, node_name: str | None):
    script = tmp_path / "tailscale"
    if node_name is None:
        body = "#!/bin/sh\nexit 1\n"
    else:
        body = (
            "#!/bin/sh\n"
            'if [ "$1" = whois ]; then\n'
            f'  echo \'{{"Node": {{"ComputedName": "{node_name}", "Name": "{node_name}.tail123.ts.net."}}}}\'\n'
            "  exit 0\nfi\n"
            'if [ "$1" = ip ]; then echo 100.100.1.1; exit 0; fi\nexit 1\n'
        )
    script.write_text(body)
    os.chmod(script, 0o700)
    return script


@pytest.mark.parametrize(
    "node, ok",
    [("controle", True), ("CONTROLE", True), ("intruso", False), (None, False)],
)
def test_whois_required(agent, tmp_path, monkeypatch, node, ok):
    monkeypatch.setenv("PONTE_TAILSCALE", str(fake_tailscale(tmp_path, node)))
    h = agent(verify_tailscale="sempre")
    if ok:
        assert h.client().ping()
    else:
        with pytest.raises(RemoteError):
            h.client().ping()
        assert h.audit[-1]["evento"] == "whois_recusado"


def test_whois_auto_skips_loopback(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("PONTE_TAILSCALE", str(fake_tailscale(tmp_path, "intruso")))
    h = agent(verify_tailscale="auto")
    assert h.client().ping()  # 127.0.0.1 não é IP do tailnet, então "auto" não confere


def test_whois_sempre_without_binary(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("PONTE_TAILSCALE", str(tmp_path / "nao-existe"))
    h = agent(verify_tailscale="sempre")
    with pytest.raises(RemoteError, match="não foi achado"):
        h.client().ping()


def test_whois_node_names_parsing():
    from ponte.core.tailscale import in_tailnet, node_names

    data = {"Node": {"ComputedName": "Cass-Dell", "Name": "cass-dell.tail.ts.net.", "Hostinfo": {"Hostname": "x"}}}
    assert node_names(data) == {"cass-dell", "x"}
    assert node_names({}) == set()
    assert node_names({"Node": "lixo"}) == set()
    assert in_tailnet("100.64.0.1") and in_tailnet("fd7a:115c:a1e0::1")
    assert not in_tailnet("127.0.0.1") and not in_tailnet("lixo")


def test_whois_cache(tmp_path, monkeypatch):
    from ponte.core.tailscale import WhoisChecker

    counter = tmp_path / "count"
    script = tmp_path / "tailscale"
    script.write_text(f'#!/bin/sh\necho x >> {counter}\necho \'{{"Node": {{"ComputedName": "a"}}}}\'\n')
    os.chmod(script, 0o700)
    monkeypatch.setenv("PONTE_TAILSCALE", str(script))
    w = WhoisChecker(ttl=60)
    for _ in range(5):
        w.check("sempre", "100.64.0.5", "a")
    assert counter.read_text().count("x") == 1
    assert wait_until(lambda: True)
