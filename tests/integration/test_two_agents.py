"""Dois agentes de verdade (processos separados) em loopback, usados pela CLI.

Cada "máquina" tem seu RELAY_DIR. A configura e pareia tudo pela própria CLI
(caminho manual com segredo), como você faria nas duas máquinas.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from relay.core import config as config_mod

from ..helpers import free_port

SRC = Path(__file__).resolve().parents[2] / "src"

TICKER = """
\"\"\"Conta devagar.\"\"\"
import sys, time
from relay.command import params
PARAMETROS = [{"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 50, "padrao": 3}]
CATEGORIA = "Teste"
for i in range(params()["vezes"]):
    print(f"tique {i}", flush=True)
    time.sleep(0.3)
print("pronto", file=sys.stderr)
"""


class Machine:
    def __init__(self, root: Path, name: str):
        self.name = name
        self.root = root
        self.port = free_port()
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(root / "home"),
            "RELAY_DIR": str(root / "relay"),
            "RELAY_STATE_DIR": str(root / "estado"),
            "RELAY_DATA_DIR": str(root / "dados"),
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.proc: subprocess.Popen | None = None
        (root / "home").mkdir(parents=True)

    def relay(self, *args, stdin: str | None = None, timeout: float = 60, check: bool = True):
        r = subprocess.run(
            [sys.executable, "-m", "relay.cli", *args],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and r.returncode != 0:
            raise AssertionError(f"relay {' '.join(args)} saiu com {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def start_agent(self):
        self.log = open(self.root / "agente.log", "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "relay.agent"], env=self.env, stdout=self.log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError((self.root / "agente.log").read_text())

    def stop_agent(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(10)
        if self.proc:
            self.log.close()

    def make_cmd(self, name: str, code: str):
        p = self.root / "relay" / "comandos" / f"{name}.py"
        p.write_text(code)
        os.chmod(p, 0o600)


@pytest.fixture
def machines(tmp_path):
    a = Machine(tmp_path / "a", "maquina-a")
    b = Machine(tmp_path / "b", "maquina-b")
    for m in (a, b):
        m.relay("init", "--nome", m.name, "--escutar", "127.0.0.1", "--porta", str(m.port))

        def loopback(cfg):
            cfg.agent.allowed = ["127.0.0.0/8"]

        config_mod.update(loopback, Path(m.env["RELAY_DIR"]) / "relay.json")
    # Pareamento manual nas duas direções, cada uma com seu segredo.
    for agent_side, ctrl_side in ((a, b), (b, a)):
        out = agent_side.relay("agente", "autorizar", ctrl_side.name).stdout
        secret = next(line.split(":", 1)[1].strip() for line in out.splitlines() if "segredo:" in line)
        ctrl_side.relay(
            "maquina",
            "adicionar",
            agent_side.name,
            "--endereco",
            "127.0.0.1",
            "--porta",
            str(agent_side.port),
            "--segredo",
            "-",
            stdin=secret + "\n",
        )
    for m in (a, b):
        m.make_cmd("ticker", TICKER)
        m.start_agent()
    yield a, b
    for m in (a, b):
        m.stop_agent()


def test_both_directions(machines):
    a, b = machines
    assert "maquina-b respondeu" in a.relay("ping", "maquina-b").stdout
    assert "maquina-a respondeu" in b.relay("ping", "a").stdout  # pedaço do nome basta


def test_examples_listed(machines):
    a, _b = machines
    out = a.relay("listar", "b").stdout
    for name in ("status", "notificar", "travar_tela", "abrir_url", "ticker"):
        assert name in out
    assert "pede confirmação" in out
    assert "parâmetros: texto, urgente" in out


def test_run_status_example(machines):
    """O `status` de exemplo é seguro de rodar (só lê /proc)."""
    a, _b = machines
    r = a.relay("rodar", "b", "status")
    assert "memória" in r.stdout and "disco" in r.stdout


def test_run_with_params_and_live_output(machines):
    a, _ = machines
    r = a.relay("rodar", "b", "ticker", "-p", "vezes=4")
    assert r.stdout == "".join(f"tique {i}\n" for i in range(4))
    assert r.stderr == "pronto\n"
    r = a.relay("rodar", "b", "ticker", "2")  # argumento solto vira o primeiro parâmetro
    assert r.stdout == "tique 0\ntique 1\n"


def test_bad_param_from_cli(machines):
    a, _ = machines
    r = a.relay("rodar", "b", "ticker", "-p", "vezes=999", check=False)
    assert r.returncode == 1
    assert "entre 1 e 50" in r.stderr


def test_confirmation_required(machines):
    a, _ = machines
    r = a.relay("rodar", "b", "travar_tela", check=False)
    assert r.returncode == 2
    assert "pede confirmação" in r.stderr


def test_detached_cancel_status_history(machines):
    a, _ = machines
    r = a.relay("rodar", "b", "ticker", "-p", "vezes=50", "--sem-esperar")
    job = r.stdout.split("job ", 1)[1].split()[0]
    time.sleep(0.5)
    assert "cancelamento pedido" in a.relay("cancelar", "b", job).stdout
    r = a.relay("status", "b", job, "--seguir", check=False)
    assert r.returncode == 130
    assert "tique 0" in r.stdout
    hist = a.relay("historico", "b").stdout
    assert job in hist and "cancelado" in hist


def test_ctrl_c_cancels_remote_job(machines):
    a, _b = machines
    p = subprocess.Popen(
        [sys.executable, "-m", "relay.cli", "rodar", "b", "ticker", "-p", "vezes=50"],
        env=a.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert p.stdout.readline() == "tique 0\n"
    p.send_signal(signal.SIGINT)
    _out, err = p.communicate(timeout=20)
    assert p.returncode == 130
    assert "cancelando" in err
    assert "cancelado" in a.relay("historico", "b").stdout


def test_unpaired_third_machine_is_refused(machines, tmp_path):
    _a, b = machines
    c = Machine(tmp_path / "c", "maquina-c")
    c.relay("init", "--nome", "maquina-c", "--escutar", "127.0.0.1", "--porta", str(c.port))
    c.relay(
        "maquina",
        "adicionar",
        "maquina-b",
        "--endereco",
        "127.0.0.1",
        "--porta",
        str(b.port),
        "--segredo",
        "-",
        stdin="ab" * 32 + "\n",
    )
    r = c.relay("ping", "maquina-b", check=False)
    assert r.returncode == 4
    assert "recusou sem assinar" in r.stderr


def test_revoke_takes_effect_without_restart(machines):
    a, b = machines
    b.relay("agente", "revogar", "maquina-a")
    r = a.relay("ping", "b", check=False)
    assert r.returncode == 4
    assert "maquina-a respondeu" in b.relay("ping", "a").stdout  # a outra direção continua


def test_local_commands_listing(machines):
    a, _ = machines
    out = a.relay("comandos").stdout
    assert "ticker" in out and "Teste" in out


def test_config_hides_secrets(machines):
    a, _ = machines
    out = a.relay("config").stdout
    assert "(escondido)" in out
    cfg = config_mod.load(Path(a.env["RELAY_DIR"]) / "relay.json")
    for m in cfg.machines.values():
        assert m.secret.hex() not in out


def test_pairing_by_code_between_processes(tmp_path):
    """Só com 'relay parear' dos dois lados, sem copiar segredo nenhum."""
    a = Machine(tmp_path / "pa", "maquina-a")
    b = Machine(tmp_path / "pb", "maquina-b")
    for m in (a, b):
        m.relay("init", "--nome", m.name, "--escutar", "127.0.0.1", "--porta", str(m.port), "--sem-exemplos")

        def loopback(cfg):
            cfg.agent.allowed = ["127.0.0.0/8"]

        config_mod.update(loopback, Path(m.env["RELAY_DIR"]) / "relay.json")
    a.start_agent()
    b.start_agent()
    pair_port = free_port()
    try:
        window = subprocess.Popen(
            [sys.executable, "-m", "relay.cli", "parear", "--escutar", "127.0.0.1", "--porta", str(pair_port)],
            env=b.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        code = None
        for _ in range(20):
            line = window.stdout.readline()
            if "código:" in line:
                code = line.split("código:", 1)[1].strip()
                break
        assert code and len(code.replace(" ", "")) == 8
        r = a.relay("parear", "127.0.0.1", "--porta", str(pair_port), stdin=code + "\n")
        assert "agora mandam uma na outra" in r.stdout
        out, err = window.communicate(timeout=10)
        assert window.returncode == 0, out + err
        assert "maquina-a e esta máquina agora mandam uma na outra" in out
        # Ainda falta a porta certa do agente no lado A? Não: ela veio no pareamento.
        assert "maquina-b respondeu" in a.relay("ping", "maquina-b").stdout
        # O mesmo pareamento já abriu a outra direção.
        assert "maquina-a respondeu" in b.relay("ping", "maquina-a").stdout
        # E o resumo mostra as duas direções e que a outra está online.
        resumo = a.relay().stdout
        assert "Esta máquina: maquina-a" in resumo
        assert "esta manda nela e ela manda nesta" in resumo and "online" in resumo
    finally:
        a.stop_agent()
        b.stop_agent()


def test_pairing_wrong_code_from_cli(tmp_path):
    a = Machine(tmp_path / "wa", "maquina-a")
    b = Machine(tmp_path / "wb", "maquina-b")
    for m in (a, b):
        m.relay("init", "--nome", m.name, "--escutar", "127.0.0.1", "--porta", str(m.port), "--sem-exemplos")

        def loopback(cfg):
            cfg.agent.allowed = ["127.0.0.0/8"]

        config_mod.update(loopback, Path(m.env["RELAY_DIR"]) / "relay.json")
    pair_port = free_port()
    window = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "relay.cli",
            "parear",
            "--escutar",
            "127.0.0.1",
            "--porta",
            str(pair_port),
            "--tempo",
            "20",
        ],
        env=b.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    code = None
    for _ in range(20):
        line = window.stdout.readline()
        if "código:" in line:
            code = line.split("código:", 1)[1].strip().replace(" ", "")
            break
    wrong = "00000000" if code != "00000000" else "11111111"
    for _ in range(3):
        r = a.relay("parear", "127.0.0.1", "--porta", str(pair_port), stdin=wrong + "\n", check=False)
        assert r.returncode == 4 and "código errado" in r.stderr
    out, _ = window.communicate(timeout=10)
    assert window.returncode == 1
    assert "3 tentativas erradas" in out
    assert config_mod.load(Path(b.env["RELAY_DIR"]) / "relay.json").agent.controllers == {}


IMG_CMD = (
    '"""Devolve uma imagem."""\n'
    "import os\n"
    'open(os.environ["RELAY_OUTPUT_FILE"], "wb").write(b"\\x89PNG\\r\\n\\x1a\\n" + b"img" * 50)\n'
    'print("imagem pronta")\n'
)


def test_run_saves_returned_image(machines, tmp_path):
    a, b = machines
    b.make_cmd("tela", IMG_CMD)
    dest = tmp_path / "saidas"
    dest.mkdir()
    r = a.relay("rodar", "b", "tela", "--salvar", str(dest))
    assert "imagem pronta" in r.stdout
    assert "arquivo salvo em" in r.stdout
    saved = list(dest.glob("tela-*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes().startswith(b"\x89PNG")
