"""Protocolo (frames, chaves, assinatura) e config v2."""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest

from ponte.core import config as config_mod
from ponte.core import protocol
from ponte.core.errors import ConfigError, ProtocolError


def test_directional_keys_differ():
    s = os.urandom(32)
    assert protocol.derive_key(s, "a", "b") != protocol.derive_key(s, "b", "a")
    assert protocol.derive_key(s, "a", "b") != protocol.derive_key(s, "a", "c")
    # Sem ambiguidade na concatenação dos nomes.
    assert protocol.derive_key(s, "ab", "c") != protocol.derive_key(s, "a", "bc")


def test_sign_verify_kinds():
    key = os.urandom(32)
    msg = protocol.sign(key, protocol.KIND_REQUEST, {"a": 1, "b": "ç"})
    assert protocol.verify(key, protocol.KIND_REQUEST, msg)
    assert not protocol.verify(key, protocol.KIND_RESPONSE, msg)
    assert not protocol.verify(os.urandom(32), protocol.KIND_REQUEST, msg)
    msg["b"] = "c"
    assert not protocol.verify(key, protocol.KIND_REQUEST, msg)


def test_canonical_ignores_key_order_and_mac():
    a = protocol.canonical({"b": 1, "a": 2, "mac": "x"})
    b = protocol.canonical({"a": 2, "b": 1})
    assert a == b == b'{"a":2,"b":1}'


def _pair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    return a, b


def test_frames_roundtrip():
    a, b = _pair()
    protocol.send_frame(a, {"x": "ã" * 10})
    assert protocol.recv_frame(b, 1000) == {"x": "ã" * 10}


@pytest.mark.parametrize(
    "raw, msg",
    [
        (b"\x00\x00\x00\x00", "vazio"),
        (b"\x00\x10\x00\x00", "grande demais"),
        (b"\x00\x00\x00\x02[]", "objeto"),
        (b"\x00\x00\x00\x03NaN", "JSON"),
        (b"\x00\x00\x00\x02\xc3(", "JSON"),
        (b"\x00\x00\x00\x09", "fechada"),
    ],
)
def test_bad_frames(raw, msg):
    a, b = _pair()
    a.sendall(raw)
    a.shutdown(socket.SHUT_WR)
    with pytest.raises(ProtocolError, match=msg):
        protocol.recv_frame(b, 1000)


def test_send_frame_respects_limit():
    a, _ = _pair()
    with pytest.raises(ProtocolError):
        protocol.send_frame(a, {"x": "y" * 100}, limit=10)


def test_nonce_cache_expiry_and_limit():
    c = protocol.NonceCache(ttl=10, max_size=2)
    assert c.add("a", now=0)
    assert not c.add("a", now=1)
    assert c.add("b", now=2)
    assert not c.add("c", now=3)  # cheio: falha fechada
    assert c.add("c", now=20)  # "a" e "b" expiraram


def test_responder_and_verifier_agree():
    a, b = _pair()
    key = os.urandom(32)
    out = protocol.Responder(sock=a, key=key, agent="ag", controller="ct", nonce="n" * 32)
    v = protocol.ResponseVerifier(key=key, controller="ct", agent="ag", nonce="n" * 32)
    out.send("aceito", job="1")
    out.send("fim", fim=True)
    assert v.check(protocol.recv_frame(b, 10000))["seq"] == 0
    assert v.check(protocol.recv_frame(b, 10000))["fim"] is True
    assert v.done
    with pytest.raises(ProtocolError):
        out.send("x")


def test_read_magic_limits():
    a, b = _pair()
    a.sendall(b"X" * 100)
    assert len(protocol.read_magic(b)) == protocol.MAX_MAGIC
    a, b = _pair()
    a.sendall(b"{rest")
    assert protocol.read_magic(b) == b"{"


# --------------------------------------------------------------------------
# config


def test_config_roundtrip(tmp_path):
    cfg = config_mod.default_config("Cass-Dell-Brasil.local")
    assert cfg.name == "cass-dell-brasil"
    cfg.agent.controllers["g15"] = config_mod.ControllerEntry("g15", os.urandom(32), ["status"], "cass-g15")
    cfg.machines["g15"] = config_mod.MachineEntry("g15", "g15.tail.ts.net", 47800, os.urandom(32))
    path = tmp_path / "ponte.json"
    config_mod.save(cfg, path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    loaded = config_mod.load(path)
    assert loaded.to_json() == cfg.to_json()
    assert loaded.agent.controllers["g15"].allows("status")
    assert not loaded.agent.controllers["g15"].allows("travar_tela")


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda d: d.update(versao=1), "versão"),
        (lambda d: d.update(nome="Com Espaço"), "nome"),
        (lambda d: d["agente"].update(porta=70000), "porta"),
        (lambda d: d["agente"].update(permitidos=["lixo"]), "rede inválida"),
        (lambda d: d["agente"].update(verificar_tailscale="talvez"), "verificar_tailscale"),
        (lambda d: d["agente"].update(max_paralelo=0), "max_paralelo"),
        (lambda d: d["agente"].update(escutar="nao-e-ip"), "escutar"),
        (lambda d: d["agente"]["controladores"].update(x={"segredo": "ab"}), "32 bytes"),
        (lambda d: d["agente"]["controladores"].update(x={"segredo": "zz" * 32}), "hexadecimal"),
        (
            lambda d: d["agente"]["controladores"].update(x={"segredo": "ab" * 32, "comandos": "tudo"}),
            "comandos",
        ),
        (lambda d: d["maquinas"].update(x={"segredo": "ab" * 32, "endereco": ""}), "endereco"),
    ],
)
def test_config_validation(mutate, msg):
    data = config_mod.default_config("a").to_json()
    mutate(data)
    with pytest.raises(ConfigError, match=msg):
        config_mod.Config.from_json(data)


def test_config_requires_mode_600(tmp_path):
    path = tmp_path / "ponte.json"
    config_mod.save(config_mod.default_config("a"), path)
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        config_mod.load(path)


def test_config_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "ponte.json"
    path.write_text('{"versao": 2, "nome": "a", "nome": "b"}')
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError, match="repetida"):
        config_mod.load(path)


def test_find_machine_partial():
    cfg = config_mod.default_config("a")
    for n in ("cass-dell-brasil", "cass-dell-g15-5511"):
        cfg.machines[n] = config_mod.MachineEntry(n, n, 1, os.urandom(32))
    assert cfg.find_machine("g15").name == "cass-dell-g15-5511"
    assert cfg.find_machine("BRASIL").name == "cass-dell-brasil"
    with pytest.raises(ConfigError, match="mais de uma"):
        cfg.find_machine("cass")
    with pytest.raises(ConfigError, match="não conheço"):
        cfg.find_machine("zzz")


def test_update_is_locked_and_validated(tmp_path):
    path = tmp_path / "ponte.json"
    config_mod.save(config_mod.default_config("a"), path)

    def add(i):
        def change(cfg):
            cfg.machines[f"m{i}"] = config_mod.MachineEntry(f"m{i}", "x", 1, os.urandom(32))

        config_mod.update(change, path)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(config_mod.load(path).machines) == 10

    def bad(cfg):
        cfg.name = "NOME RUIM"

    with pytest.raises(ConfigError):
        config_mod.update(bad, path)
    assert config_mod.load(path).name == "a"


def test_config_source_reloads_and_fails_closed(tmp_path):
    path = tmp_path / "ponte.json"
    config_mod.save(config_mod.default_config("a"), path)
    errors = []
    src = config_mod.ConfigSource(path, on_error=errors.append)
    assert src.get().name == "a"
    cfg = config_mod.default_config("b")
    config_mod.save(cfg, path)
    assert src.get().name == "b"
    path.write_text("{quebrado")
    os.chmod(path, 0o600)
    assert src.get() is None
    assert errors
    config_mod.save(config_mod.default_config("c"), path)
    assert src.get().name == "c"
    assert json.loads(path.read_text())["nome"] == "c"


def test_config_dir_must_not_be_writable_by_others(tmp_path):
    folder = tmp_path / "cfg"
    folder.mkdir(mode=0o700)
    path = folder / "ponte.json"
    config_mod.save(config_mod.default_config("a"), path)
    os.chmod(folder, 0o777)
    try:
        with pytest.raises(ConfigError, match="chmod 700"):
            config_mod.load(path)
    finally:
        os.chmod(folder, 0o700)
    assert config_mod.load(path).name == "a"


def test_duplicate_key_error_does_not_echo_the_key():
    from ponte.core.legacy import loads_strict

    with pytest.raises(ValueError) as e:
        loads_strict('{"segredo-gigante": 1, "segredo-gigante": 2}')
    assert "segredo-gigante" not in str(e.value)
