"""SPAKE2 e pareamento por código: o código certo funciona, o resto não."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from ponte.core import config as config_mod
from ponte.core import pairing, protocol, spake2
from ponte.core.errors import AuthError, ConnectionFailed, PonteError, ProtocolError

# ---------------------------------------------------------------------------
# SPAKE2


def run_pake(code_a: str, code_b: str, id_a="a", id_b="b"):
    a = spake2.Spake2("A", code_a, id_a, id_b)
    b = spake2.Spake2("B", code_b, id_a, id_b)
    a.finish(b.message())
    b.finish(a.message())
    return a, b


def test_same_code_same_key():
    a, b = run_pake("12345678", "12345678")
    assert a.check_peer(b.my_confirmation()) and b.check_peer(a.my_confirmation())
    assert a.derive("x") == b.derive("x")
    assert a.derive("x") != a.derive("y")
    assert len(a.derive("x", 64)) == 64


def test_wrong_code_fails_confirmation():
    a, b = run_pake("12345678", "12345679")
    assert not a.check_peer(b.my_confirmation())
    assert not b.check_peer(a.my_confirmation())
    assert a.derive("x") != b.derive("x")


def test_names_are_bound():
    a = spake2.Spake2("A", "11112222", "a", "b")
    b = spake2.Spake2("B", "11112222", "a", "outro")
    a.finish(b.message())
    b.finish(a.message())
    assert not a.check_peer(b.my_confirmation())


def test_messages_are_random():
    assert spake2.Spake2("A", "1", "a", "b").message() != spake2.Spake2("A", "1", "a", "b").message()


def test_group_constants():
    assert pow(spake2.G, spake2.Q, spake2.P) == 1
    assert spake2.is_element(spake2.M) and spake2.is_element(spake2.N) and spake2.M != spake2.N
    assert pow(2, spake2.P - 1, spake2.P) == 1  # sanidade: Fermat


@pytest.mark.parametrize(
    "bad",
    [
        "00" * 256,
        "00" * 255 + "01",
        (spake2.P - 1).to_bytes(256, "big").hex(),
        spake2.P.to_bytes(256, "big").hex(),
        "zz" * 256,
        "ab",
        5,
        None,
    ],
)
def test_bad_elements_rejected(bad):
    with pytest.raises(ValueError):
        spake2.Spake2("A", "12345678", "a", "b").finish(bad)


def test_non_subgroup_element_rejected():
    # 11 não é quadrado módulo p, então está fora do subgrupo de ordem q.
    assert pow(11, spake2.Q, spake2.P) != 1
    with pytest.raises(ValueError, match="fora do grupo"):
        spake2.decode((11).to_bytes(256, "big").hex())


def test_confirmation_needs_finish():
    with pytest.raises(RuntimeError):
        spake2.Spake2("A", "1", "a", "b").my_confirmation()


@pytest.mark.parametrize("bad", ["", "x" * 64, "00" * 31, 7, None])
def test_check_peer_bad_values(bad):
    a, _ = run_pake("1", "1")
    assert a.check_peer(bad) is False


# ---------------------------------------------------------------------------
# códigos


def test_codes():
    codes = {pairing.generate_code() for _ in range(50)}
    assert all(len(c) == 8 and c.isdigit() for c in codes) and len(codes) > 40
    assert pairing.format_code("12345678") == "1234 5678"
    assert pairing.normalize_code(" 1234-5678 ") == "12345678"
    for bad in ("1234", "1234567x", "123456789", "abcdefgh"):
        with pytest.raises(PonteError):
            pairing.normalize_code(bad)


# ---------------------------------------------------------------------------
# janela de pareamento de verdade, em loopback


def agent_config(tmp_path, name="agente"):
    cfg = config_mod.Config(name=name)
    cfg.agent.allowed = ["127.0.0.0/8"]
    cfg.agent.verify_tailscale = "nunca"
    cfg.agent.port = 47999
    path = tmp_path / f"{name}.json"
    config_mod.save(cfg, path)
    return cfg, path


def open_window(tmp_path, **kw):
    cfg, path = agent_config(tmp_path)
    events = []
    w = pairing.PairingWindow(cfg, "127.0.0.1", port=0, on_event=events.append, **kw)
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", w.serve()), daemon=True)
    t.start()
    return w, t, box, events, path


def test_pairing_success_both_sides(tmp_path):
    w, t, box, events, agent_path = open_window(tmp_path)
    ctl_cfg, ctl_path = agent_config(tmp_path, "controle")
    paired = pairing.pair_with("controle", "127.0.0.1", w.port, pairing.format_code(w.code))
    t.join(5)
    result = box["r"]
    assert result.controller == "controle" and result.secret == paired.secret
    assert paired.name == "agente" and paired.port == 47999 and len(paired.secret) == 32
    pairing.save_controller(result, agent_path)
    pairing.save_machine(paired, ctl_path)
    a = config_mod.load(agent_path)
    c = config_mod.load(ctl_path)
    assert a.agent.controllers["controle"].secret == c.machines["agente"].secret
    assert any("pareado com controle" in e for e in events)


def test_paired_secret_actually_works(agent, tmp_path):
    """Depois de parear, o agente aceita pedidos assinados com o segredo novo."""
    h = agent()
    cfg = config_mod.load(h.config_path)
    cfg.agent.port = h.port  # o agente de teste escuta numa porta escolhida na hora
    w = pairing.PairingWindow(cfg, "127.0.0.1", port=0)
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", w.serve()), daemon=True)
    t.start()
    paired = pairing.pair_with("novo", "127.0.0.1", w.port, w.code)
    t.join(5)
    pairing.save_controller(box["r"], h.config_path)
    assert h.client(name="novo", secret=paired.secret).ping().machine == "agente"


def test_wrong_code_counts_and_closes(tmp_path):
    w, t, box, events, _ = open_window(tmp_path)
    wrong = "00000000" if w.code != "00000000" else "11111111"
    for _ in range(3):
        with pytest.raises(AuthError, match="código errado"):
            pairing.pair_with("controle", "127.0.0.1", w.port, wrong)
    t.join(5)
    assert box["r"] is None
    assert w.failures == 3
    assert any("tentativas erradas" in e for e in events)
    with pytest.raises(ConnectionFailed):
        pairing.pair_with("controle", "127.0.0.1", w.port, w.code)  # janela já fechou


def test_window_times_out(tmp_path):
    w, t, box, events, _ = open_window(tmp_path, seconds=0.5)
    t.join(5)
    assert box["r"] is None and "o tempo acabou" in events


def test_window_refuses_ip_outside_list(tmp_path):
    cfg, _ = agent_config(tmp_path)
    cfg.agent.allowed = ["100.64.0.0/10"]
    events = []
    w = pairing.PairingWindow(cfg, "127.0.0.1", port=0, seconds=2, on_event=events.append)
    t = threading.Thread(target=w.serve, daemon=True)
    t.start()
    with pytest.raises((ProtocolError, ConnectionFailed)):
        pairing.pair_with("controle", "127.0.0.1", w.port, w.code)
    w.stop()
    t.join(5)
    assert w.failures == 0 and any("fora da lista" in e for e in events)


def test_window_checks_tailscale_node(tmp_path, monkeypatch):
    script = tmp_path / "tailscale"
    script.write_text('#!/bin/sh\necho \'{"Node": {"ComputedName": "intruso"}}\'\n')
    os.chmod(script, 0o700)
    monkeypatch.setenv("PONTE_TAILSCALE", str(script))
    cfg, _ = agent_config(tmp_path)
    cfg.agent.verify_tailscale = "sempre"
    w = pairing.PairingWindow(cfg, "127.0.0.1", port=0, seconds=5)
    t = threading.Thread(target=w.serve, daemon=True)
    t.start()
    with pytest.raises(AuthError, match="intruso"):
        pairing.pair_with("controle", "127.0.0.1", w.port, w.code)
    w.stop()
    t.join(5)
    assert w.failures == 1


def test_window_refuses_own_name(tmp_path):
    w, t, box, _, _ = open_window(tmp_path)
    with pytest.raises(AuthError, match="ela mesma"):
        pairing.pair_with("agente", "127.0.0.1", w.port, w.code)
    w.stop()
    t.join(5)


def raw_attempt(port: int, frames: list[dict]) -> list[dict]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(pairing.MAGIC)
        assert pairing._read_magic(s) == pairing.MAGIC
        out = []
        for f in frames:
            protocol.send_frame(s, f)
            try:
                out.append(protocol.recv_frame(s, 4096))
            except ProtocolError:
                break
        return out


@pytest.mark.parametrize(
    "hello",
    [
        {"tipo": "ola", "de": "controle", "X": "00" * 256},
        {"tipo": "ola", "de": "controle", "X": "lixo"},
        {"tipo": "ola", "de": "Nome Ruim", "X": spake2.Spake2("A", "1", "a", "b").message()},
        {"tipo": "outra", "de": "controle"},
    ],
)
def test_bad_hello_counts_as_failure(tmp_path, hello):
    w, t, box, _, _ = open_window(tmp_path)
    replies = raw_attempt(w.port, [hello])
    assert replies and replies[0]["tipo"] == "erro"
    assert w.failures == 1
    w.stop()
    t.join(5)


def test_dropping_after_agent_proof_counts(tmp_path):
    """Quem pega a confirmação do agente e some (testando um código) gasta uma tentativa."""
    w, t, box, _, _ = open_window(tmp_path)
    a = spake2.Spake2("A", "99999999", "controle", "agente")
    replies = raw_attempt(w.port, [{"tipo": "ola", "de": "controle", "X": a.message()}])
    assert replies[0]["tipo"] == "ola" and "Y" in replies[0]
    assert "segredo" not in str(replies[0])
    assert wait_failures(w, 1)
    w.stop()
    t.join(5)


def wait_failures(w, n, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if w.failures >= n:
            return True
        time.sleep(0.02)
    return False


def test_tampered_final_message_rejected(tmp_path, monkeypatch):
    """Se alguém trocar a porta na mensagem final, o controlador percebe."""
    w, t, box, _, _ = open_window(tmp_path)
    real_send = protocol.send_frame

    def tamper(sock, obj, limit=protocol.MAX_RESPONSE_FRAME):
        if obj.get("tipo") == "pronto":
            obj = {**obj, "porta": 1}
        return real_send(sock, obj, limit)

    monkeypatch.setattr(pairing.protocol, "send_frame", tamper)
    with pytest.raises(AuthError, match="confirmação final"):
        pairing.pair_with("controle", "127.0.0.1", w.port, w.code)
    t.join(5)


def test_not_a_pairing_port():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(100)
            conn.sendall(b"PONTE/2\n")
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(ProtocolError, match="janela de pareamento"):
        pairing.pair_with("controle", "127.0.0.1", srv.getsockname()[1], "12345678")


def test_repairing_keeps_permissions(tmp_path):
    cfg, path = agent_config(tmp_path)
    cfg.agent.controllers["controle"] = config_mod.ControllerEntry("controle", os.urandom(32), ["status"])
    config_mod.save(cfg, path)
    new_secret = os.urandom(32)
    pairing.save_controller(pairing.PairingResult("controle", new_secret, "127.0.0.1"), path)
    entry = config_mod.load(path).agent.controllers["controle"]
    assert entry.secret == new_secret and entry.commands == ["status"]
