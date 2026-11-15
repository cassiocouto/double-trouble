"""Testes que fixam o comportamento do ponte.py v1 (fase 0).

Servem de rede de seguranca: o v2 precisa manter as garantias que estao
aqui, ou trocar por uma melhor e registrar no DECISIONS.md. Bugs achados no
v1 ficam marcados com xfail(strict=True), entao se alguem "consertar" o v1
sem querer o teste avisa.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

from .conftest import alive, wait_until

ECHO = '''
"""Ecoa os argumentos."""
import sys
ACEITA_ARGS = True
print("args=" + "|".join(sys.argv[1:]))
sys.exit(3 if "falha" in sys.argv else 0)
'''

SIMPLE = '''
"""Diz oi."""
print("oi")
'''


# --------------------------------------------------------------------------
# caminho feliz


def test_ping_signed_and_bound_to_nonce(v1):
    v1.start()
    req = v1.build({"acao": "ping"})
    resp = v1.send(req)
    assert resp["ok"] is True
    assert resp["nonce"] == req["nonce"]
    assert v1.signed(resp)


def test_listar_reads_metadata(v1):
    v1.start()
    v1.make_cmd("eco", ECHO)
    v1.make_cmd("oi", SIMPLE)
    resp = v1.ask({"acao": "listar"})
    by_name = {c["nome"]: c for c in resp["comandos"]}
    assert by_name["eco"]["aceita_args"] is True
    assert by_name["eco"]["descricao"] == "Ecoa os argumentos."
    assert by_name["oi"]["aceita_args"] is False
    assert by_name["oi"]["timeout"] == 60


def test_rodar_returns_output_and_code(v1):
    v1.start()
    v1.make_cmd("eco", ECHO)
    resp = v1.ask({"acao": "rodar", "comando": "eco", "args": ["a b", "falha"]})
    assert resp["stdout"] == "args=a b|falha\n"
    assert resp["codigo"] == 3
    assert resp["ok"] is False
    assert v1.signed(resp)


def test_args_reach_command_without_shell(v1):
    v1.start()
    v1.make_cmd("eco", ECHO)
    evil = "$(touch /tmp/ponte-v1-pwned); `id`; rm -rf ~"
    resp = v1.ask({"acao": "rodar", "comando": "eco", "args": [evil]})
    assert resp["stdout"] == f"args={evil}\n"


def test_caller_name_in_env(v1):
    v1.start()
    v1.make_cmd("quem", 'import os\nprint(os.environ["PONTE_DE"])\n')
    resp = v1.ask({"acao": "rodar", "comando": "quem", "args": []})
    assert resp["stdout"].strip() == "testador"


# --------------------------------------------------------------------------
# autenticacao (invariante 3)


def test_wrong_secret_gets_unsigned_error(v1):
    v1.start()
    resp = v1.ask({"acao": "ping"}, key=os.urandom(32))
    assert resp == {"v": 1, "ok": False, "erro": "assinatura invalida"}
    assert "mac" not in resp


def test_tampered_request_rejected(v1):
    v1.start()
    v1.make_cmd("eco", ECHO)
    req = v1.build({"acao": "rodar", "comando": "eco", "args": ["a"]})
    req["args"] = ["b"]
    resp = v1.send(req)
    assert resp["erro"] == "assinatura invalida"
    assert "mac" not in resp


def test_missing_mac_rejected(v1):
    v1.start()
    resp = v1.send({"v": 1, "acao": "ping", "ts": int(time.time()), "nonce": "0" * 32})
    assert resp["erro"] == "assinatura invalida"


def test_replay_rejected(v1):
    v1.start()
    req = v1.build({"acao": "ping"})
    assert v1.send(req)["ok"] is True
    again = v1.send(req)
    assert again["ok"] is False
    assert again["erro"] == "pedido repetido"


def test_old_timestamp_rejected(v1):
    v1.start()
    resp = v1.ask({"acao": "ping"}, ts=int(time.time()) - 3600)
    assert resp["ok"] is False
    assert "velho" in resp["erro"]


def test_future_timestamp_rejected(v1):
    v1.start()
    resp = v1.ask({"acao": "ping"}, ts=int(time.time()) + 3600)
    assert resp["ok"] is False


def test_bad_nonce_rejected(v1):
    v1.start()
    resp = v1.ask({"acao": "ping"}, nonce="nao-e-hex")
    assert resp["erro"] == "nonce invalido"


def only_legacy_fails(request, v1, reason):
    """O bug existe no ponte.py original. No pacote ele foi corrigido."""
    if v1.impl == "legacy":
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=reason))


def test_v1_bug_non_ascii_mac_gets_signed_reply(request, v1):
    only_legacy_fails(request, v1, "BUG v1: mac nao-ASCII faz a resposta de erro sair assinada")
    v1.start()
    req = {"v": 1, "acao": "ping", "ts": int(time.time()), "nonce": "a" * 32, "mac": "é"}
    resp = v1.send(req)
    # O certo seria nao assinar. No v1 sai assinado (oraculo de assinatura).
    assert "mac" not in resp


def test_v1_bug_nan_timestamp(request, v1):
    only_legacy_fails(request, v1, "BUG v1: ts=NaN passa pela checagem de horario")
    v1.start()
    req = v1.build({"acao": "ping"}, ts=float("nan"))
    resp = v1.send(req)
    # O pacote nem aceita NaN no JSON (nao responde). Recusar com erro tambem vale.
    assert resp is None or resp["ok"] is False


# --------------------------------------------------------------------------
# entrada maliciosa e robustez (invariantes 10 e 11)


def test_huge_message_dropped_and_server_survives(v1):
    v1.start()
    out = v1.raw(b"{" + b"a" * (70 * 1024))
    assert out == b""
    assert v1.ask({"acao": "ping"})["ok"] is True


@pytest.mark.parametrize("junk", [b"nao e json\n", b"[1,2,3]\n", b"\xff\xfe\n", b"\n", b'{"v": 1\n'])
def test_invalid_json_dropped_and_server_survives(v1, junk):
    v1.start()
    assert v1.raw(junk) == b""
    assert v1.ask({"acao": "ping"})["ok"] is True


def test_unknown_action(v1):
    v1.start()
    assert v1.ask({"acao": "shell", "cmd": "id"})["erro"] == "acao desconhecida"


# --------------------------------------------------------------------------
# so roda o que esta na pasta (invariantes 1, 2 e 7)


@pytest.mark.parametrize(
    "name", ["../x", "a/b", "/etc/passwd", "X", ".oculto", "a b", "", "a" * 65, "eco.py"]
)
def test_bad_command_names(v1, name):
    v1.start()
    v1.make_cmd("eco", ECHO)
    resp = v1.ask({"acao": "rodar", "comando": name, "args": []})
    assert resp["ok"] is False
    assert "codigo" not in resp


def test_command_name_not_string(v1):
    v1.start()
    resp = v1.ask({"acao": "rodar", "comando": ["eco"], "args": []})
    assert resp["erro"] == "nome de comando invalido"


def test_missing_command(v1):
    v1.start()
    resp = v1.ask({"acao": "rodar", "comando": "nao_existe", "args": []})
    assert "nao existe" in resp["erro"]


def test_symlink_command_refused(v1, tmp_path):
    v1.start()
    target = tmp_path / "fora.py"
    target.write_text('print("fora")\n')
    os.chmod(target, 0o600)
    (v1.cmds / "link.py").symlink_to(target)
    resp = v1.ask({"acao": "rodar", "comando": "link", "args": []})
    assert "link simbolico" in resp["erro"]


@pytest.mark.parametrize("mode", [0o602, 0o620, 0o666, 0o777])
def test_writable_command_refused(v1, mode):
    v1.start()
    v1.make_cmd("aberto", SIMPLE, mode=mode)
    resp = v1.ask({"acao": "rodar", "comando": "aberto", "args": []})
    assert "permissoes abertas" in resp["erro"]


@pytest.mark.parametrize("which", ["base", "comandos"])
def test_writable_dirs_refused(v1, which):
    v1.start()
    v1.make_cmd("oi", SIMPLE)
    d = v1.base if which == "base" else v1.cmds
    os.chmod(d, 0o777)
    try:
        resp = v1.ask({"acao": "rodar", "comando": "oi", "args": []})
    finally:
        os.chmod(d, 0o700)
    assert "permissoes abertas" in resp["erro"]


@pytest.mark.skipif(os.geteuid() != 0, reason="precisa de root pra trocar o dono do arquivo")
def test_command_owned_by_other_user_refused(v1):
    v1.start()
    p = v1.make_cmd("alheio", SIMPLE)
    os.chown(p, 65534, 65534)
    resp = v1.ask({"acao": "rodar", "comando": "alheio", "args": []})
    assert "outro usuario" in resp["erro"]


def test_syntax_error_command(v1):
    v1.start()
    v1.make_cmd("quebrado", "def (:\n")
    resp = v1.ask({"acao": "rodar", "comando": "quebrado", "args": []})
    assert "erro de sintaxe" in resp["erro"]
    listed = {c["nome"]: c for c in v1.ask({"acao": "listar"})["comandos"]}
    assert "bloqueado" in listed["quebrado"]


def test_metadata_read_without_executing(v1, tmp_path):
    v1.start()
    marker = tmp_path / "executou"
    code = (
        f'"""Nao deveria rodar no listar."""\n'
        f"open({str(marker)!r}, 'w').write('x')\n"
        f"ACEITA_ARGS = __import__('os').system('touch {marker}') == 0\n"
        f"TIMEOUT = 5\n"
    )
    v1.make_cmd("efeito", code)
    listed = {c["nome"]: c for c in v1.ask({"acao": "listar"})["comandos"]}
    assert listed["efeito"]["aceita_args"] is False  # expressao nao literal e ignorada
    assert listed["efeito"]["timeout"] == 5
    assert not marker.exists()


def test_config_with_open_permissions_refused(v1):
    v1.start()
    os.chmod(v1.base / "config.json", 0o640)
    with pytest.raises(SystemExit):
        v1.mod.load_config()


# --------------------------------------------------------------------------
# argumentos (invariante 8)


def test_args_to_command_that_does_not_accept(v1):
    v1.start()
    v1.make_cmd("oi", SIMPLE)
    resp = v1.ask({"acao": "rodar", "comando": "oi", "args": ["x"]})
    assert "nao aceita argumentos" in resp["erro"]


@pytest.mark.parametrize(
    "args",
    [["a\x00b"], ["x"] * 33, ["a" * 4097], "texto", [1, 2], [None]],
)
def test_bad_args_refused(v1, args):
    v1.start()
    v1.make_cmd("eco", ECHO)
    resp = v1.ask({"acao": "rodar", "comando": "eco", "args": args})
    assert resp["ok"] is False
    assert "argumentos invalidos" in resp["erro"]


# --------------------------------------------------------------------------
# processos, tempo e saida (invariantes 9 e 10)


def test_timeout_kills_grandchildren(v1, tmp_path):
    v1.start()
    pidfile = tmp_path / "neto.pid"
    v1.make_cmd(
        "demora",
        f"""
import subprocess, time
TIMEOUT = 1
p = subprocess.Popen(["sleep", "300"])
open({str(pidfile)!r}, "w").write(str(p.pid))
time.sleep(300)
""",
    )
    t0 = time.monotonic()
    resp = v1.ask({"acao": "rodar", "comando": "demora", "args": []})
    assert resp["estourou_tempo"] is True
    assert resp["ok"] is False
    assert time.monotonic() - t0 < 10
    pid = int(pidfile.read_text())
    assert wait_until(lambda: not alive(pid))


def test_leftover_child_does_not_hold_response(v1, tmp_path):
    """O comando termina e deixa um filho segurando o stdout.

    No v1 a resposta volta logo (saida vai pra arquivo), mas o filho continua
    vivo pra sempre, sem timeout nenhum. O v2 muda isso (ver DECISIONS.md).
    """
    v1.start()
    pidfile = tmp_path / "filho.pid"
    v1.make_cmd(
        "solta",
        f"""
import subprocess
p = subprocess.Popen(["sleep", "30"])
open({str(pidfile)!r}, "w").write(str(p.pid))
print("tchau")
""",
    )
    t0 = time.monotonic()
    resp = v1.ask({"acao": "rodar", "comando": "solta", "args": []})
    assert time.monotonic() - t0 < 5
    assert resp["stdout"] == "tchau\n"
    pid = int(pidfile.read_text())
    if v1.impl == "pacote":
        # Mudanca de proposito (DECISIONS.md): o grupo morre quando o comando acaba.
        assert wait_until(lambda: not alive(pid))
        return
    try:
        assert alive(pid), "comportamento v1: o filho fica vivo"
    finally:
        os.kill(pid, 9)


def test_output_truncated(v1):
    v1.start()
    v1.make_cmd("falante", 'import sys\nsys.stdout.write("x" * (300 * 1024))\n')
    resp = v1.ask({"acao": "rodar", "comando": "falante", "args": []})
    assert resp["truncado"] is True
    assert len(resp["stdout"]) == 256 * 1024


def test_parallel_limit(v1):
    v1.start()
    v1.make_cmd("lento", "import time\ntime.sleep(2)\n")
    results = []

    def go():
        results.append(v1.ask({"acao": "rodar", "comando": "lento", "args": []}))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.8)
    extra = v1.ask({"acao": "rodar", "comando": "lento", "args": []})
    for t in threads:
        t.join()
    assert "ja tem 4 comandos rodando" in extra["erro"]
    assert all(r["ok"] for r in results)


# --------------------------------------------------------------------------
# rede (invariante 5)


def test_ip_outside_allowlist_gets_nothing(v1):
    v1.start(permitidos=["100.64.0.0/10"])
    req = v1.build({"acao": "ping"})
    assert v1.raw(json.dumps(req).encode() + b"\n") == b""


def test_ip_allowed_helper(v1):
    v1.start(permitidos=["100.64.0.0/10", "fd7a:115c:a1e0::/48"])
    ok = v1.srv.ip_allowed
    assert ok("100.100.1.2")
    assert ok("::ffff:100.100.1.2")
    assert ok("fd7a:115c:a1e0::1")
    assert not ok("127.0.0.1")
    assert not ok("192.168.0.10")
    assert not ok("lixo")


# --------------------------------------------------------------------------
# cliente


def _fake_server(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(reply)
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def _client_cfg(v1, port):
    return {"nome": "cli", "escutar": "127.0.0.1", "porta": port, "_key": v1.key, "maquinas": {}}


def test_client_rejects_unsigned_response(v1):
    port = _fake_server(json.dumps({"v": 1, "ok": True, "nonce": "x"}).encode() + b"\n")
    with pytest.raises(SystemExit) as e:
        v1.mod.call(_client_cfg(v1, port), "local", {"acao": "ping"}, 5)
    assert e.value.code == 4


def test_client_rejects_response_for_other_nonce(v1):
    body = v1.mod.sign(v1.key, "resp", {"v": 1, "ok": True, "nonce": "b" * 32, "de": "x"})
    port = _fake_server(json.dumps(body).encode() + b"\n")
    with pytest.raises(SystemExit) as e:
        v1.mod.call(_client_cfg(v1, port), "local", {"acao": "ping"}, 5)
    assert e.value.code == 4


def test_client_rejects_request_signature_reused_as_response(v1):
    """Uma mensagem assinada como pedido nao vale como resposta."""
    req = v1.build({"acao": "ping"})
    port = _fake_server(json.dumps(req).encode() + b"\n")
    with pytest.raises(SystemExit) as e:
        v1.mod.call(_client_cfg(v1, port), "local", {"acao": "ping"}, 5)
    assert e.value.code == 4
