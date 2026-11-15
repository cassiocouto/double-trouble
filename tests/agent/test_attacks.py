"""Testes de ataque contra o agente v2, um bloco por invariante.

Cada teste tenta quebrar uma regra de docs/SEGURANCA.md e confere que ela segurou.
"""

from __future__ import annotations

import os
import secrets
import socket
import struct
import threading
import time

import pytest

from ponte.core import config as config_mod
from ponte.core import protocol
from ponte.core.errors import AuthError, RemoteError

from ..conftest import alive, wait_until

ECHO = '"""Ecoa."""\nimport sys\nACEITA_ARGS = True\nprint("|".join(sys.argv[1:]))\n'
PARAMS = """
import json, sys
PARAMETROS = [
    {"nome": "texto", "tipo": "texto", "obrigatorio": True, "max": 20, "regex": "[a-z ]+"},
    {"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 3, "padrao": 1},
    {"nome": "alto", "tipo": "booleano"},
    {"nome": "modo", "tipo": "escolha", "opcoes": ["a", "b"]},
]
print(json.dumps(json.load(sys.stdin), sort_keys=True))
print(sys.argv[1:])
"""


def unsigned(frames):
    assert len(frames) == 1
    f = frames[0]
    assert "mac" not in f, "erro pra quem não autenticou nunca é assinado"
    assert "nonce" not in f
    assert f["autenticado"] is False
    return f


def signed_error(h, frames, req):
    assert len(frames) == 1
    f = frames[0]
    assert protocol.verify(h.key(), protocol.KIND_RESPONSE, f)
    assert f["nonce"] == req["nonce"]
    assert f["tipo"] == "erro"
    return f


# ==========================================================================
# Invariante 1: só arquivos da pasta de comandos, nada de shell


@pytest.mark.parametrize("action", ["shell", "exec", "eval", "rodar_codigo", "", None, 5, ["rodar"]])
def test_inv1_no_free_actions(agent, action):
    h = agent()
    req = h.build("ping")
    req["acao"] = action
    protocol.sign(h.key(), protocol.KIND_REQUEST, req)
    f = signed_error(h, h.raw_frames(req), req)
    assert f["erro"] == "ação desconhecida"


@pytest.mark.parametrize(
    "name", ["../x", "../../bin/sh", "/bin/sh", "sub/cmd", "a b", "X", ".x", "x.py", "", "a" * 65, "x\x00"]
)
def test_inv1_command_names_outside_folder(agent, name, tmp_path):
    h = agent()
    (tmp_path / "x.py").write_text("print('fora')\n")
    with pytest.raises(RemoteError, match="nome de comando inválido"):
        h.client().run(name)


def test_inv1_args_never_reach_a_shell(agent, tmp_path):
    h = agent()
    h.make_cmd("eco", ECHO)
    marker = tmp_path / "pwned"
    evil = [f"; touch {marker}", f"$(touch {marker})", f"`touch {marker}`", "| id", "&& id", "'\"\\"]
    out = h.client().run("eco", args=evil)
    assert out.text_stdout == "|".join(evil) + "\n"
    assert not marker.exists()


def test_inv1_only_python_files_in_folder(agent):
    h = agent()
    sh = h.base / "comandos" / "script.sh"
    sh.write_text("#!/bin/sh\necho oi\n")
    os.chmod(sh, 0o700)
    with pytest.raises(RemoteError, match="não existe"):
        h.client().run("script")
    with pytest.raises(RemoteError, match="nome de comando inválido"):
        h.client().run("script.sh")


def test_inv1_symlink_to_outside_refused(agent, tmp_path):
    h = agent()
    outside = tmp_path / "fora.py"
    outside.write_text("print('fora')\n")
    os.chmod(outside, 0o600)
    (h.base / "comandos" / "link.py").symlink_to(outside)
    with pytest.raises(RemoteError, match="link simbólico"):
        h.client().run("link")


def test_inv1_swapped_file_runs_what_was_checked(agent):
    """O arquivo é executado pelo fd conferido, não pelo caminho."""
    h = agent()
    h.make_cmd(
        "muda", 'import os\nprint(os.environ["PONTE_COMANDO"], __file__.startswith("/proc/self/fd/"))\n'
    )
    out = h.client().run("muda")
    assert out.text_stdout == "muda True\n"


# ==========================================================================
# Invariante 2: metadados só com ast


def test_inv2_listing_never_executes(agent, tmp_path):
    h = agent()
    marker = tmp_path / "executou"
    h.make_cmd(
        "armadilha",
        f'"""Armadilha."""\nopen({str(marker)!r}, "w").write("x")\n'
        f"PARAMETROS = [__import__('os').system('touch {marker}')]\n",
    )
    h.make_cmd(
        "armadilha2",
        f"TIMEOUT = __import__('os').system('touch {marker}')\nCONFIRMAR = exec('x = 1')\n",
    )
    listing = {c["nome"]: c for c in h.client().list_commands()}
    assert not marker.exists()
    assert "não é um valor literal" in listing["armadilha"]["bloqueado"]
    assert "bloqueado" not in listing["armadilha2"]
    assert listing["armadilha2"]["timeout"] == 60
    assert listing["armadilha2"]["confirmar"] is False
    assert "avisos" in listing["armadilha2"]


# ==========================================================================
# Invariante 3: tudo autenticado, erro pra estranho sem assinatura


def test_inv3_wrong_secret(agent):
    h = agent()
    with pytest.raises(AuthError, match="recusou sem assinar"):
        h.client(secret=os.urandom(32)).ping()
    assert h.audit[-1]["evento"] == "nao_autenticado"


def test_inv3_unknown_controller(agent):
    h = agent()
    req = h.build("ping", controller="intruso", key=os.urandom(32))
    f = unsigned(h.raw_frames(req))
    assert f["erro"] == protocol.UNAUTHENTICATED


def test_inv3_unknown_controller_and_bad_mac_look_the_same(agent):
    """Não dá pra descobrir nomes de controladores pela mensagem de erro."""
    h = agent()
    a = unsigned(h.raw_frames(h.build("ping", controller="intruso", key=os.urandom(32))))
    b = unsigned(h.raw_frames(h.build("ping", key=os.urandom(32))))
    assert a == b


@pytest.mark.parametrize("field, value", [("acao", "listar"), ("ts", 1), ("de", "outro"), ("extra", 1)])
def test_inv3_tampered_request(agent, field, value):
    h = agent()
    req = h.build("ping")
    req[field] = value
    unsigned(h.raw_frames(req))


@pytest.mark.parametrize("mac", [None, "", "é" * 64, "0" * 64, "zz" * 32, "0" * 63, 123, ["a"]])
def test_inv3_bad_mac_values(agent, mac):
    h = agent()
    req = h.build("ping")
    if mac is None:
        del req["mac"]
    else:
        req["mac"] = mac
    unsigned(h.raw_frames(req))


def test_inv3_request_signed_with_response_kind(agent):
    h = agent()
    req = h.build("ping")
    protocol.sign(h.key(), protocol.KIND_RESPONSE, req)
    unsigned(h.raw_frames(req))


def test_inv3_replay(agent):
    h = agent()
    req = h.build("ping")
    first = h.raw_frames(req)
    assert first[0]["tipo"] == "pong"
    f = signed_error(h, h.raw_frames(req), req)
    assert f["codigo_erro"] == "repetido"


@pytest.mark.parametrize(
    "ts", [lambda: int(time.time()) - 3600, lambda: int(time.time()) + 3600, lambda: True]
)
def test_inv3_bad_timestamps(agent, ts):
    h = agent()
    req = h.build("ping")
    req["ts"] = ts()
    protocol.sign(h.key(), protocol.KIND_REQUEST, req)
    f = signed_error(h, h.raw_frames(req), req)
    assert f["codigo_erro"] == "relogio"


def test_inv3_nan_timestamp_never_accepted(agent):
    h = agent()
    req = h.build("ping")
    body = protocol.encode_frame(req).replace(f'"ts":{req["ts"]}'.encode(), b'"ts":NaN')
    body = struct.pack(">I", len(body) - 4) + body[4:]
    f = unsigned(h.raw_frames(body))
    assert f["erro"] == "pedido ilegível"


@pytest.mark.parametrize("nonce", ["curto", "Z" * 32, "a" * 31, 7])
def test_inv3_bad_nonce(agent, nonce):
    h = agent()
    req = h.build("ping")
    req["nonce"] = nonce
    protocol.sign(h.key(), protocol.KIND_REQUEST, req)
    frames = h.raw_frames(req)
    assert frames[0]["codigo_erro"] == "nonce"


def test_inv3_wrong_destination(agent):
    """Pedido pra outra máquina, mesmo assinado com uma chave válida, é recusado."""
    h = agent()
    other_key = protocol.derive_key(h.secret, h.controller, "outra-maquina")
    req = protocol.build_request(other_key, h.controller, "outra-maquina", "ping")
    unsigned(h.raw_frames(req))  # chave derivada pra outro agente nem confere aqui
    req = h.build("ping")
    req["para"] = "outra"
    protocol.sign(h.key(), protocol.KIND_REQUEST, req)
    assert h.raw_frames(req)[0]["codigo_erro"] == "destino"


def test_inv3_reflection_between_directions(agent):
    """Mesmo segredo nas duas direções (migração do v1): pedido de A->B não vale em B->A."""
    h = agent(name="b", controller="a")
    shared = h.secret
    # Alguém captura um pedido de B pra A (assinado com a chave B->A) e manda pro B.
    req = protocol.build_request(protocol.derive_key(shared, "b", "a"), "b", "a", "ping")
    unsigned(h.raw_frames(req))


# ==========================================================================
# Invariante 4: respostas autenticadas, amarradas ao nonce e em ordem


class FakeAgent:
    """Servidor que responde o que o teste mandar, pra atacar o cliente."""

    def __init__(self, frames_for):
        self.frames_for = frames_for
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(5)
            protocol.read_magic(conn)
            conn.sendall(protocol.MAGIC)
            req = protocol.recv_frame(conn, protocol.MAX_REQUEST_FRAME)
            for f in self.frames_for(req):
                conn.sendall(f if isinstance(f, bytes) else protocol.encode_frame(f))
        self.sock.close()


def fake_client(port, secret, controller="controle", agent_name="agente"):
    from ponte.core.client import Client
    from ponte.core.config import Config, MachineEntry

    cfg = Config(name=controller)
    m = MachineEntry(name=agent_name, address="127.0.0.1", port=port, secret=secret)
    cfg.machines[agent_name] = m
    return Client(cfg, m, read_timeout=5)


def resp(key, req, seq, tipo="saida", fim=False, **fields):
    body = {
        "v": 2,
        "de": "agente",
        "para": "controle",
        "nonce": req["nonce"],
        "seq": seq,
        "tipo": tipo,
        "fim": fim,
        **fields,
    }
    return protocol.sign(key, protocol.KIND_RESPONSE, body)


SECRET = secrets.token_bytes(32)
KEY = protocol.derive_key(SECRET, "controle", "agente")


def run_against(frames_for):
    fake = FakeAgent(frames_for)
    return fake_client(fake.port, SECRET).run("x")


def test_inv4_good_stream_is_accepted():
    out = run_against(
        lambda r: [
            resp(KEY, r, 0, "aceito", job="j"),
            resp(KEY, r, 1, dados="oi", fluxo="stdout"),
            resp(KEY, r, 2, "fim", True, resultado={"estado": "ok"}),
        ]
    )
    assert out.text_stdout == "oi"


@pytest.mark.parametrize(
    "case",
    [
        "sem_mac",
        "mac_errado",
        "seq_pulado",
        "seq_repetido",
        "seq_texto",
        "outro_nonce",
        "outra_origem",
        "outro_destino",
        "depois_do_fim",
        "chave_de_pedido",
        "outra_direcao",
        "versao",
    ],
)
def test_inv4_client_rejects_bad_frames(case):
    def frames(r):
        good0 = resp(KEY, r, 0, "aceito", job="j")
        if case == "sem_mac":
            bad = {k: v for k, v in resp(KEY, r, 1, dados="x", fluxo="stdout").items() if k != "mac"}
        elif case == "mac_errado":
            bad = resp(KEY, r, 1, dados="x", fluxo="stdout")
            bad["dados"] = "trocado"
        elif case == "seq_pulado":
            bad = resp(KEY, r, 2, dados="x", fluxo="stdout")
        elif case == "seq_repetido":
            bad = resp(KEY, r, 0, dados="x", fluxo="stdout")
        elif case == "seq_texto":
            bad = resp(KEY, r, "1", dados="x", fluxo="stdout")
        elif case == "outro_nonce":
            bad = resp(KEY, {"nonce": "b" * 32}, 1, dados="x", fluxo="stdout")
        elif case == "outra_origem":
            bad = resp(KEY, r, 1, dados="x", fluxo="stdout")
            bad["de"] = "intruso"
            protocol.sign(KEY, protocol.KIND_RESPONSE, bad)
        elif case == "outro_destino":
            bad = resp(KEY, r, 1, dados="x", fluxo="stdout")
            bad["para"] = "outro"
            protocol.sign(KEY, protocol.KIND_RESPONSE, bad)
        elif case == "depois_do_fim":
            return [
                good0,
                resp(KEY, r, 1, "fim", True, resultado={}),
                resp(KEY, r, 2, dados="x", fluxo="stdout"),
            ]
        elif case == "chave_de_pedido":
            bad = resp(KEY, r, 1, dados="x", fluxo="stdout")
            protocol.sign(KEY, protocol.KIND_REQUEST, bad)
        elif case == "outra_direcao":
            other = protocol.derive_key(SECRET, "agente", "controle")
            bad = resp(other, r, 1, dados="x", fluxo="stdout")
        elif case == "versao":
            bad = resp(KEY, r, 1, dados="x", fluxo="stdout")
            bad["v"] = 3
            protocol.sign(KEY, protocol.KIND_RESPONSE, bad)
        return [good0, bad, resp(KEY, r, 2, "fim", True, resultado={})]

    if case == "depois_do_fim":
        # O cliente para no fim e nem lê o resto. O que importa é não aceitar nada depois.
        out = run_against(frames)
        assert out.text_stdout == ""
        v = protocol.ResponseVerifier(KEY, "controle", "agente", "a" * 32)
        v.done = True
        with pytest.raises(AuthError, match="depois do fim"):
            v.check({})
        return
    with pytest.raises(AuthError):
        run_against(frames)


def test_inv4_connection_cut_mid_stream_is_an_error():
    from ponte.core.errors import ConnectionFailed

    with pytest.raises(ConnectionFailed, match="no meio"):
        run_against(lambda r: [resp(KEY, r, 0, "aceito", job="j")])


def test_inv4_unsigned_error_is_only_shown(agent):
    h = agent()
    with pytest.raises(AuthError) as e:
        h.client(secret=os.urandom(32)).run("x")
    assert "recusou sem assinar" in str(e.value)


# ==========================================================================
# Invariante 5: IP fora da lista não chega a ser lido


def test_inv5_ip_outside_allowlist(agent):
    h = agent(allowed=["100.64.0.0/10"])
    t0 = time.monotonic()
    with socket.create_connection(("127.0.0.1", h.port), timeout=5) as s:
        # Nada foi mandado e o agente já fechou: não leu nada.
        assert s.recv(10) == b""
    assert time.monotonic() - t0 < 2
    assert h.audit[-1] == {"evento": "ip_recusado", "ip": "127.0.0.1"}


def test_inv5_allowlist_reloads(agent):
    h = agent()
    assert h.client().ping()

    def change(cfg):
        cfg.agent.allowed = ["100.64.0.0/10"]

    h.update(change)
    from ponte.core.errors import ProtocolError

    with pytest.raises(ProtocolError, match="fechou a conexão"):
        h.client().ping()


def test_inv5_ip_allowed_helper(agent):
    h = agent(allowed=["100.64.0.0/10", "fd7a:115c:a1e0::/48"])
    ok = h.srv.ip_allowed
    assert ok("100.101.1.2") and ok("::ffff:100.101.1.2") and ok("fd7a:115c:a1e0::5")
    assert not ok("127.0.0.1") and not ok("10.0.0.1") and not ok("fe80::1%eth0") and not ok("x")


def test_inv5_listen_address_never_wildcard():
    from ponte.core.errors import ConfigError

    cfg = config_mod.default_config("x").to_json()
    for bad in ("0.0.0.0", "::"):
        cfg["agente"]["escutar"] = bad
        with pytest.raises(ConfigError, match="não pode ser"):
            config_mod.Config.from_json(cfg)


# ==========================================================================
# Invariante 6: nunca como root


def test_inv6_refuses_root(tmp_path, monkeypatch):
    from ponte.agent import server
    from ponte.core.errors import PonteError

    from ..helpers import AgentHarness

    monkeypatch.setattr(server.os, "geteuid", lambda: 0)
    with pytest.raises(PonteError, match="não roda como root"):
        AgentHarness(tmp_path / "a")


# ==========================================================================
# Invariante 7: dono e permissões


def test_inv7_config_must_be_private(agent):
    h = agent()
    assert h.client().ping()
    os.chmod(h.config_path, 0o640)
    # Falha fechada: com o config inseguro o agente nem tem lista de IPs, e fecha tudo.
    from ponte.core.errors import ProtocolError

    with pytest.raises(ProtocolError, match="fechou a conexão"):
        h.client().ping()
    assert "permissões abertas" in h.source.error
    os.chmod(h.config_path, 0o600)
    os.utime(h.config_path)
    assert h.client().ping()


def test_inv7_config_symlink_refused(agent, tmp_path):
    from ponte.core.errors import ConfigError

    h = agent()
    link = tmp_path / "link.json"
    link.symlink_to(h.config_path)
    with pytest.raises(ConfigError, match="link simbólico"):
        config_mod.load(link)


@pytest.mark.parametrize("which", ["base", "comandos", "arquivo"])
def test_inv7_open_permissions_block_commands(agent, which):
    h = agent()
    p = h.make_cmd("oi", "print('oi')\n")
    target = {"base": h.base, "comandos": h.base / "comandos", "arquivo": p}[which]
    os.chmod(target, 0o777 if which != "arquivo" else 0o666)
    try:
        with pytest.raises(RemoteError, match="permissões abertas"):
            h.client().run("oi")
    finally:
        os.chmod(target, 0o700 if which != "arquivo" else 0o600)


# ==========================================================================
# Invariante 8: argumentos e parâmetros


def test_inv8_args_to_command_that_does_not_accept(agent):
    h = agent()
    h.make_cmd("oi", "print('oi')\n")
    with pytest.raises(RemoteError, match="não aceita argumentos"):
        h.client().run("oi", args=["x"])


@pytest.mark.parametrize("args", [["a\x00b"], ["x"] * 33, ["a" * 4097], "texto", [1], [None]])
def test_inv8_bad_args(agent, args):
    h = agent()
    h.make_cmd("eco", ECHO)
    req = h.build("rodar", comando="eco", args=args)
    frames = h.raw_frames(req)
    assert frames[0]["codigo_erro"] == "args"


def test_inv8_params_arrive_on_stdin_validated(agent):
    h = agent()
    h.make_cmd("p", PARAMS)
    out = h.client().run("p", params={"texto": "oi mundo", "alto": True})
    assert out.text_stdout == '{"alto": true, "modo": null, "texto": "oi mundo", "vezes": 1}\n[]\n'


@pytest.mark.parametrize(
    "params, msg",
    [
        ({}, "falta o parâmetro obrigatório"),
        ({"texto": 5}, "precisa ser texto"),
        ({"texto": "MAIUSCULO"}, "formato esperado"),
        ({"texto": "a" * 21}, "passou de 20"),
        ({"texto": "oi\x00"}, "byte nulo"),
        ({"texto": "oi\nmundo"}, "caracteres de controle"),
        ({"texto": "oi", "vezes": 9}, "entre 1 e 3"),
        ({"texto": "oi", "vezes": "2"}, "número inteiro"),
        ({"texto": "oi", "vezes": 2.0}, "número inteiro"),
        ({"texto": "oi", "vezes": True}, "número inteiro"),
        ({"texto": "oi", "alto": "sim"}, "verdadeiro ou falso"),
        ({"texto": "oi", "modo": "c"}, "uma destas opções"),
        ({"texto": "oi", "intruso": 1}, "parâmetro desconhecido"),
        (["texto"], "precisam ser um objeto"),
    ],
)
def test_inv8_bad_params(agent, params, msg):
    h = agent()
    h.make_cmd("p", PARAMS)
    with pytest.raises(RemoteError, match=msg):
        h.client().run("p", params=params)


def test_inv8_args_to_params_command(agent):
    h = agent()
    h.make_cmd("p", PARAMS)
    with pytest.raises(RemoteError, match="usa PARAMETROS"):
        h.client().run("p", params={"texto": "oi"}, args=["--x"])


def test_inv8_params_to_plain_command(agent):
    h = agent()
    h.make_cmd("eco", ECHO)
    with pytest.raises(RemoteError, match="não declara PARAMETROS"):
        h.client().run("eco", params={"a": 1})


def test_inv8_broken_schema_blocks_command(agent):
    h = agent()
    h.make_cmd("ruim", 'PARAMETROS = [{"nome": "x", "tipo": "arquivo"}]\nprint("rodou")\n')
    with pytest.raises(RemoteError, match="PARAMETROS inválido"):
        h.client().run("ruim")


def test_inv8_params_not_visible_in_argv(agent):
    """Valores vão pelo stdin, então outro usuário não vê no `ps`."""
    h = agent()
    h.make_cmd(
        "argv",
        'import sys, json\nPARAMETROS = [{"nome": "s", "tipo": "texto", "segredo": True}]\n'
        "print(json.load(sys.stdin)['s'], len(sys.argv))\n",
    )
    out = h.client().run("argv", params={"s": "senha123"})
    assert out.text_stdout == "senha123 1\n"
    rodar = next(e for e in h.audit if e["evento"] == "rodar")
    assert rodar["parametros"] == {"s": "***"}


# ==========================================================================
# Invariante 9: timeouts e grupos de processo


GRANDCHILD = """
import subprocess, time
TIMEOUT = {timeout}
p = subprocess.Popen(["sleep", "300"])
open({pidfile!r}, "w").write(str(p.pid))
time.sleep(300)
"""


def test_inv9_timeout_kills_grandchildren(agent, tmp_path):
    h = agent()
    pidfile = str(tmp_path / "neto")
    h.make_cmd("demora", GRANDCHILD.format(timeout=1, pidfile=pidfile))
    out = h.client().run("demora")
    assert out.result["estado"] == "tempo_esgotado"
    assert wait_until(lambda: not alive(int(open(pidfile).read())))


def test_inv9_disconnect_does_not_orphan(agent, tmp_path):
    """O controlador cai no meio. O job segue com o timeout e o neto morre."""
    h = agent()
    pidfile = str(tmp_path / "neto")
    h.make_cmd("demora", GRANDCHILD.format(timeout=2, pidfile=pidfile))
    c = h.client()
    frames = c.request("rodar", comando="demora", seguir=True)
    job = next(frames)["job"]
    assert wait_until(lambda: os.path.exists(pidfile) and os.path.getsize(pidfile) > 0)
    c.abort()
    with pytest.raises(Exception):
        list(frames)
    pid = int(open(pidfile).read())
    assert alive(pid)
    assert wait_until(lambda: not alive(pid), timeout=10)
    status = h.client().status(job)
    assert status.result["estado"] == "tempo_esgotado"


def test_inv9_cancel_kills_group(agent, tmp_path):
    h = agent()
    pidfile = str(tmp_path / "neto")
    h.make_cmd("demora", GRANDCHILD.format(timeout=600, pidfile=pidfile))
    c = h.client()
    seen = {}

    def on_event(ev):
        if ev["tipo"] == "aceito":
            seen["job"] = ev["job"]

            def later():
                wait_until(lambda: os.path.exists(pidfile) and os.path.getsize(pidfile) > 0)
                h.client().cancel(ev["job"])

            threading.Thread(target=later).start()

    t0 = time.monotonic()
    out = c.run("demora", on_event=on_event)
    assert out.result["estado"] == "cancelado"
    assert time.monotonic() - t0 < 10
    assert wait_until(lambda: not alive(int(open(pidfile).read())))


def test_inv9_leftover_child_dies_with_command(agent, tmp_path):
    h = agent()
    pidfile = str(tmp_path / "filho")
    h.make_cmd(
        "solta",
        f'import subprocess\np = subprocess.Popen(["sleep", "300"])\nopen({pidfile!r}, "w").write(str(p.pid))\n',
    )
    t0 = time.monotonic()
    out = h.client().run("solta")
    assert out.result["estado"] == "ok"
    assert time.monotonic() - t0 < 6
    assert wait_until(lambda: not alive(int(open(pidfile).read())))


def test_inv9_agent_shutdown_kills_running_jobs(agent, tmp_path):
    h = agent()
    pidfile = str(tmp_path / "neto")
    h.make_cmd("demora", GRANDCHILD.format(timeout=600, pidfile=pidfile))
    h.client().run("demora", follow=False)
    assert wait_until(lambda: os.path.exists(pidfile) and os.path.getsize(pidfile) > 0)
    h.srv.jobs.shutdown()
    assert wait_until(lambda: not alive(int(open(pidfile).read())))


# ==========================================================================
# Invariante 10: limites de tamanho


def test_inv10_huge_length_prefix_is_not_read(agent):
    h = agent()
    data = h.raw(protocol.MAGIC + struct.pack(">I", 2**31) + b"x" * 1000)
    assert data == protocol.MAGIC + protocol.encode_frame(protocol.unsigned_error("pedido ilegível"))


def test_inv10_frame_just_over_limit(agent):
    h = agent()
    req = h.build("ping", lixo="x" * protocol.MAX_REQUEST_FRAME)
    frames = h.raw_frames(req)
    assert frames[0]["erro"] == "pedido ilegível"
    assert h.client().ping()


def test_inv10_output_truncated(agent):
    h = agent()
    h.make_cmd("falante", 'import sys\nsys.stdout.write("x" * (300 * 1024))\nprint("fim", file=sys.stderr)\n')
    out = h.client().run("falante")
    assert len(out.text_stdout) == 256 * 1024
    assert out.text_stderr == "fim\n"
    assert out.result["truncado"] is True


def test_inv10_long_magic_line(agent):
    h = agent()
    assert h.raw(b"PONTE/" + b"9" * 100) == protocol.MAGIC


def test_inv10_client_refuses_huge_response_frame():
    from ponte.core.errors import ProtocolError

    with pytest.raises(ProtocolError, match="grande demais"):
        run_against(lambda r: [struct.pack(">I", protocol.MAX_RESPONSE_FRAME + 1)])


# ==========================================================================
# Invariante 11: nada derruba o agente


@pytest.mark.parametrize(
    "junk",
    [
        b"",
        b"\x00\x00\x00\x00",
        b"\x00\x00\x00\x05abc",
        b"\x00\x00\x00\x03[1]",
        b"\x00\x00\x00\x02\xff\xfe",
        b'\x00\x00\x00\x0c{"a":1,"a":2',
        b'\x00\x00\x00\x0e{"a":1,"a":2}',
        b"\x00\x00\x00\x02{}",
    ],
)
def test_inv11_garbage_after_magic(agent, junk):
    h = agent()
    h.raw(protocol.MAGIC + junk, timeout=20)
    assert h.client().ping()


@pytest.mark.parametrize("junk", [b"GET / HTTP/1.1\r\n\r\n", b"\xff" * 50, b"PONTE/1\n", b"\n"])
def test_inv11_garbage_instead_of_magic(agent, junk):
    h = agent()
    h.raw(junk)
    assert h.client().ping()


def test_inv11_internal_error_is_signed_and_survives(agent, monkeypatch):
    h = agent()
    from ponte.agent import server

    def boom(*a, **k):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(server.commands, "list_commands", boom)
    with pytest.raises(RemoteError, match="erro interno"):
        h.client().list_commands()
    monkeypatch.undo()
    assert h.client().ping()


def test_inv11_slow_client_does_not_block_others(agent):
    h = agent()
    s = socket.create_connection(("127.0.0.1", h.port))
    s.sendall(protocol.MAGIC + b"\x00\x00")  # para no meio do cabeçalho
    try:
        assert h.client().ping()
    finally:
        s.close()


def test_inv11_too_many_connections(agent):
    from ponte.agent.server import MAX_CONNECTIONS

    h = agent()
    socks = []
    try:
        for _ in range(MAX_CONNECTIONS):
            socks.append(socket.create_connection(("127.0.0.1", h.port)))
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", h.port), timeout=5)
        assert extra.recv(10) == b""
        extra.close()
    finally:
        for s in socks:
            s.close()
    assert wait_until(lambda: _ping_ok(h), timeout=20)


def _ping_ok(h) -> bool:
    try:
        return bool(h.client().ping())
    except Exception:
        return False


# ==========================================================================
# Versões diferentes dão erro claro


def test_v1_client_gets_clear_error(agent):
    h = agent()
    data = h.raw(b'{"v": 1, "acao": "ping"}\n')
    import json

    reply = json.loads(data)
    assert "protocolo v2" in reply["erro"]
    assert "mac" not in reply


def test_future_client_sees_our_version(agent):
    h = agent()
    assert h.raw(b"PONTE/3\n") == protocol.MAGIC


def test_v2_client_against_v1_server(tmp_path):
    """O cliente v2 explica que do outro lado pode estar o ponte.py antigo."""
    from ponte.core.errors import ProtocolError

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def v1_like():
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(2)
            try:
                conn.recv(100)  # o v1 lê uma linha, não entende e fecha
            except OSError:
                pass
        srv.close()

    threading.Thread(target=v1_like, daemon=True).start()
    with pytest.raises(ProtocolError, match="ponte.py antigo"):
        fake_client(srv.getsockname()[1], SECRET).ping()


def test_v2_client_against_future_server():
    from ponte.core.errors import ProtocolError

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def v3():
        conn, _ = srv.accept()
        with conn:
            conn.recv(100)
            conn.sendall(b"PONTE/3\n")
        srv.close()

    threading.Thread(target=v3, daemon=True).start()
    with pytest.raises(ProtocolError, match="outra versão"):
        fake_client(srv.getsockname()[1], SECRET).ping()
