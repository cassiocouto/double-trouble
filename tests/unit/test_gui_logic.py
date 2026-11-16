"""Lógica da interface (serviços, view-models, formulário, histórico), sem tela.

Os view-models falam com agentes de verdade em loopback. Um "laço principal"
falso faz o papel do GLib: o serviço entrega os resultados numa fila e o
teste roda essa fila na thread dele, como a interface faria.
"""

from __future__ import annotations

import os
import queue
import threading
import time

import pytest

from relay.core.config import MachineEntry
from relay.gui import text
from relay.gui.form import NONE_CHOICE, FormModel, remember_values
from relay.gui.history import HistoryStore
from relay.gui.services import ControllerService, friendly_error
from relay.gui.viewmodels import CommandsVM, HistoryVM, MachinesVM, RunVM, SettingsVM


class FakeLoop:
    """Fila de callbacks que só roda na thread do teste (como o GLib.idle_add)."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.main = threading.get_ident()

    def dispatch(self, fn, *args):
        self.q.put((fn, args))

    def pump(self, until=lambda: False, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                fn, args = self.q.get(timeout=0.05)
            except queue.Empty:
                if until():
                    return True
                continue
            assert threading.get_ident() == self.main
            fn(*args)
            if until():
                return True
        return until()


@pytest.fixture
def ctx(agent, tmp_path):
    h = agent()
    loop = FakeLoop()
    cfg, _ = h.controller_config()
    service = ControllerService(loop.dispatch, load_config=lambda: cfg)
    yield h, loop, service, cfg
    service.shutdown()


SLOW = """
\"\"\"Conta devagar.\"\"\"
import sys, time
from relay.command import params
PARAMETROS = [{"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 100, "padrao": 3}]
for i in range(params()["vezes"]):
    print(f"tique {i}", flush=True)
    time.sleep(0.1)
print("fim", file=sys.stderr)
"""


# --------------------------------------------------------------------------
# máquinas


def test_machines_online_and_offline(ctx):
    _h, loop, service, cfg = ctx
    cfg.machines["sumida"] = MachineEntry("sumida", "127.0.0.1", 1, os.urandom(32))
    vm = MachinesVM(service)
    events = []
    vm.machine_changed.connect(events.append)
    vm.reload()
    assert list(vm.machines) == ["agente", "sumida"]
    vm.refresh()
    loop.pump(lambda: all(m.status != "verificando" for m in vm.machines.values()))
    on, off = vm.machines["agente"], vm.machines["sumida"]
    assert on.online and on.latency_ms is not None and on.version == "2.0.0"
    assert on.subtitle.startswith("online · ")
    assert "relógio" in on.subtitle
    assert off.status == "offline" and "Não deu pra falar" in off.error
    assert "offline" in off.subtitle
    assert "agente" in events and "sumida" in events


def test_machines_missing_config(ctx):
    from relay.core.errors import ConfigError

    _, loop, _, _ = ctx

    def boom():
        raise ConfigError("não achei o config")

    vm = MachinesVM(ControllerService(loop.dispatch, load_config=boom))
    vm.reload()
    assert vm.config_error == "Não achei o config"
    assert vm.machines == {}


def test_skew_warning():
    from relay.gui.viewmodels import MachineState

    m = MachineState("x", "x", 1, status="online", latency_ms=3, skew=45.0)
    assert "relógios" in m.warning
    m.skew = 2
    assert m.warning == ""


# --------------------------------------------------------------------------
# comandos


def test_commands_load_search_groups(ctx):
    h, loop, service, _ = ctx
    h.make_cmd("status", '"""Mostra memória e disco."""\nCATEGORIA = "Sistema"\n')
    h.make_cmd("notificar", '"""Mostra uma notificação."""\nCATEGORIA = "Tela"\n')
    h.make_cmd("solto", '"""Sem categoria."""\n')
    h.make_cmd("quebrado", "def (:\n")
    vm = CommandsVM(service, "agente")
    vm.load()
    assert vm.loading
    loop.pump(lambda: not vm.loading)
    assert [g for g, _ in vm.groups()] == ["Sistema", "Tela", "Outros"]
    vm.set_query("MEMORIA")  # sem acento e maiúsculo também acha
    assert [c["nome"] for c in vm.visible()] == ["status"]
    vm.set_query("tela notif")
    assert [c["nome"] for c in vm.visible()] == ["notificar"]
    assert "bloqueado" in vm.find("quebrado")


def test_commands_error(ctx):
    _, loop, service, cfg = ctx
    cfg.machines["agente"].secret = os.urandom(32)
    vm = CommandsVM(service, "agente")
    vm.load()
    loop.pump(lambda: not vm.loading)
    assert "não aceitou esta máquina" in vm.error


# --------------------------------------------------------------------------
# formulário


PARAMS = [
    {"nome": "texto", "tipo": "texto", "rotulo": "Mensagem", "obrigatorio": True, "max": 10},
    {"nome": "senha", "tipo": "texto", "segredo": True},
    {"nome": "nota", "tipo": "texto", "multilinha": True},
    {"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 5, "padrao": 2},
    {"nome": "talvez", "tipo": "inteiro"},
    {"nome": "alto", "tipo": "booleano"},
    {"nome": "modo", "tipo": "escolha", "opcoes": ["a", "b"]},
    {"nome": "lado", "tipo": "escolha", "opcoes": ["esq", "dir"], "padrao": "dir"},
]


def test_form_fields_and_kinds():
    form = FormModel({"nome": "x", "parametros": PARAMS})
    kinds = {f.name: f.kind for f in form.fields}
    assert kinds == {
        "texto": "texto",
        "senha": "segredo",
        "nota": "texto_longo",
        "vezes": "inteiro",
        "talvez": "inteiro_opcional",
        "alto": "booleano",
        "modo": "escolha",
        "lado": "escolha",
    }
    modo = next(f for f in form.fields if f.name == "modo")
    assert modo.options == [NONE_CHOICE, "a", "b"]
    values = form.initial_values()
    assert values["vezes"] == 2 and values["lado"] == "dir" and values["modo"] == NONE_CHOICE


def test_form_collect_ok():
    form = FormModel({"nome": "x", "parametros": PARAMS})
    values = form.initial_values()
    values.update(texto="oi", vezes=3.0, talvez=" 7 ", alto=True, nota="a\nb")
    params, args, errors = form.collect(values)
    assert errors == {}
    assert args is None
    assert params == {"texto": "oi", "nota": "a\nb", "vezes": 3, "talvez": 7, "alto": True, "lado": "dir"}


def test_form_collect_errors():
    form = FormModel({"nome": "x", "parametros": PARAMS})
    values = form.initial_values()
    values.update(texto="", talvez="sete", vezes=9)
    params, _, errors = form.collect(values)
    assert params is None
    assert errors["texto"] == "Campo obrigatório"
    assert "inteiro" in errors["talvez"]
    assert "entre 1 e 5" in errors["vezes"]
    values.update(texto="x" * 11, talvez="", vezes=1)
    _, _, errors = form.collect(values)
    assert list(errors) == ["texto"]
    values.update(texto="linha\nquebrada")
    _, _, errors = form.collect(values)
    assert "controle" in errors["texto"]


def test_form_legacy_args():
    form = FormModel({"nome": "x", "aceita_args": True})
    assert [f.kind for f in form.fields] == ["argumentos"]
    params, args, errors = form.collect({"args": "a b\n\n$(id)\n"})
    assert params is None and errors == {} and args == ["a b", "$(id)"]
    _, args, errors = form.collect({"args": "\n".join(["x"] * 33)})
    assert args is None and "32" in errors["args"]


def test_form_no_inputs_and_blocked():
    assert FormModel({"nome": "x"}).fields == []
    assert FormModel({"nome": "x"}).collect({}) == (None, None, {})
    assert not FormModel({"nome": "x", "bloqueado": "erro de sintaxe"}).runnable
    broken = FormModel({"nome": "x", "parametros": [{"nome": "a", "tipo": "arquivo"}]})
    assert not broken.runnable and "inválido" in broken.error


def test_form_never_prefills_or_stores_secrets():
    form = FormModel({"nome": "x", "parametros": PARAMS})
    stored = remember_values(form, {"texto": "oi", "senha": "segredo123"}, None)
    assert stored == {"texto": "oi", "senha": "***"}
    again = form.initial_values(stored)
    assert again["senha"] == "" and again["texto"] == "oi"


# --------------------------------------------------------------------------
# executar


def make_run(ctx, name, code, tmp_path, **kw):
    h, loop, service, _ = ctx
    h.make_cmd(name, code)
    vm = CommandsVM(service, "agente")
    vm.load()
    loop.pump(lambda: not vm.loading)
    store = HistoryStore(tmp_path / "hist.sqlite3")
    run = RunVM(service, "agente", vm.find(name), history=store, **kw)
    return run, store


def test_run_streams_and_saves_history(ctx, tmp_path):
    _, loop, _, _ = ctx
    run, store = make_run(ctx, "lento", SLOW, tmp_path)
    chunks = []
    run.output_added.connect(lambda s, t: chunks.append((s, t)))
    values = run.form.initial_values()
    values["vezes"] = 4
    run.request_run(values)
    assert run.state == RunVM.RUNNING
    assert run.status_text().startswith("Rodando há")
    loop.pump(lambda: run.state != RunVM.RUNNING)
    assert run.state == RunVM.DONE
    assert run.stream_text("stdout") == "".join(f"tique {i}\n" for i in range(4))
    assert run.stream_text("stderr") == "fim\n"
    assert len(chunks) >= 2
    assert run.message.startswith("Deu certo em")
    [entry] = store.list()
    assert entry.state == "ok" and entry.code == 0 and entry.values == {"vezes": 4}
    assert entry.stdout.startswith("tique 0") and entry.job == run.job


def test_run_validation_errors_never_reach_agent(ctx, tmp_path):
    h, _loop, _, _ = ctx
    run, store = make_run(ctx, "lento", SLOW, tmp_path)
    seen = []
    run.errors_changed.connect(lambda: seen.append(dict(run.errors)))
    run.request_run({"vezes": 500})
    assert run.state == RunVM.READY
    assert "entre 1 e 100" in seen[-1]["vezes"]
    assert store.list() == []
    assert not any(e["evento"] == "rodar" for e in h.audit)


def test_run_confirmation(ctx, tmp_path):
    _, loop, _, _ = ctx
    run, _ = make_run(ctx, "perigo", 'CONFIRMAR = True\nprint("feito")\n', tmp_path)
    asked = []
    run.confirm_requested.connect(asked.append)
    run.request_run({})
    assert run.state == RunVM.CONFIRMING and asked == ["Rodar “perigo” em agente?"]
    run.confirm(False)
    assert run.state == RunVM.READY
    run.request_run({})
    run.confirm(True)
    loop.pump(lambda: run.state == RunVM.DONE)
    assert run.output_text() == "feito\n"


def test_run_cancel(ctx, tmp_path):
    _, loop, _, _ = ctx
    run, store = make_run(ctx, "lento", SLOW, tmp_path)
    run.request_run({"vezes": 100})
    loop.pump(lambda: bool(run.output))
    run.cancel()
    assert run.message == "Cancelando…"
    loop.pump(lambda: run.state != RunVM.RUNNING, timeout=15)
    assert run.result["estado"] == "cancelado"
    assert run.message.startswith("Cancelado")
    assert store.list()[0].state == "cancelado"


def test_run_remote_refusal(ctx, tmp_path):
    h, loop, _, _ = ctx
    run, store = make_run(ctx, "some", "print(1)\n", tmp_path)
    (h.base / "comandos" / "some.py").unlink()
    run.request_run({})
    loop.pump(lambda: run.state != RunVM.RUNNING)
    assert run.state == RunVM.FAILED
    assert "não existe" in run.message
    assert store.list()[0].state == "erro"


def test_run_notifies_long_job_when_unfocused(ctx, tmp_path):
    _, loop, _, _ = ctx
    clock = {"t": 0.0}
    notes = []
    run, _ = make_run(
        ctx,
        "oi",
        "print(1)\n",
        tmp_path,
        notifier=lambda t, b: notes.append((t, b)),
        is_focused=lambda: False,
        clock=lambda: clock["t"],
    )
    run.request_run({})
    clock["t"] = 30.0  # finge que demorou
    loop.pump(lambda: run.state == RunVM.DONE)
    assert notes and notes[0][0] == "oi terminou em agente"
    focused, _ = make_run(
        ctx,
        "oi2",
        "print(1)\n",
        tmp_path,
        notifier=lambda t, b: notes.append(t),
        is_focused=lambda: True,
        clock=lambda: clock["t"],
    )
    focused.request_run({})
    loop.pump(lambda: focused.state == RunVM.DONE)
    assert len(notes) == 1


def test_blocked_command_cannot_run(ctx):
    _, _, service, _ = ctx
    run = RunVM(service, "agente", {"nome": "x", "bloqueado": "o arquivo tem permissões abertas demais"})
    assert run.state == RunVM.FAILED and "permissões" in run.message
    run.request_run({})
    assert run.state == RunVM.FAILED


def test_no_network_on_ui_thread(ctx):
    """O serviço nunca bloqueia quem chama: devolve na hora e entrega depois."""
    h, loop, service, _ = ctx
    h.make_cmd("lento", "import time\ntime.sleep(1)\n")
    done = []
    t0 = time.monotonic()
    service.run("agente", "lento", None, None, lambda e: None, done.append, done.append)
    assert time.monotonic() - t0 < 0.1
    loop.pump(lambda: bool(done))


# --------------------------------------------------------------------------
# histórico


def test_history_store(tmp_path):
    store = HistoryStore(tmp_path / "h" / "hist.sqlite3")
    assert oct(os.stat(store.path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(store.path.parent).st_mode & 0o777) == "0o700"
    a = store.start("g15", "status", {})
    store.finish(a, job="j1", state="ok", code=0, duration=1.5, stdout="oi", stderr="")
    b = store.start("g15", "lento", {"vezes": 3})
    store.close()
    store = HistoryStore(tmp_path / "h" / "hist.sqlite3")
    entries = store.list()
    assert [e.id for e in entries] == [b, a]
    assert entries[0].state == "desconhecido"  # janela fechou antes do fim
    vm = HistoryVM(store)
    vm.reload()
    assert vm.title(entries[1]) == "status em g15"
    assert "Deu certo" in vm.subtitle(entries[1]) and "1,5 s" in vm.subtitle(entries[1])
    vm.clear()
    assert vm.entries == []


def test_history_rerun_prefills(ctx, tmp_path):
    _, loop, service, _ = ctx
    run, store = make_run(ctx, "lento", SLOW, tmp_path)
    run.request_run({"vezes": 2})
    loop.pump(lambda: run.state == RunVM.DONE)
    entry = store.list()[0]
    again = RunVM(service, "agente", run.command, history=store, previous_values=entry.values)
    assert again.values["vezes"] == 2


# --------------------------------------------------------------------------
# configurações


def test_settings(tmp_path):
    from relay.core import config as config_mod

    path = tmp_path / "relay.json"
    cfg = config_mod.default_config("brasil")
    cfg.agent.controllers["g15"] = config_mod.ControllerEntry("g15", os.urandom(32))
    config_mod.save(cfg, path)
    vm = SettingsVM(path)
    changes = []
    vm.changed.connect(lambda: changes.append(1))
    assert vm.add_machine("G15", "", 47800, "ab" * 32) is None
    assert vm.load().machines["g15"].address == "g15"
    assert "hexadecimal" in vm.add_machine("x", "", 1, "zz")
    assert "nome" in vm.add_machine("com espaço", "", 1, "ab" * 32).lower()
    assert vm.edit_machine("g15", "g15.tail.ts.net", 47900) is None
    assert vm.load().machines["g15"].port == 47900
    assert vm.set_controller_commands("g15", ["status"]) is None
    assert vm.controller_summary(vm.load().agent.controllers["g15"]) == "só pode rodar: status"
    assert vm.revoke_controller("g15") is None
    assert vm.remove_machine("g15") is None
    loaded = vm.load()
    assert loaded.machines == {} and loaded.agent.controllers == {}
    assert len(changes) == 5


# --------------------------------------------------------------------------
# textos


def test_text_helpers():
    assert text.duration(0.25) == "0,25 s"
    assert text.duration(12.34) == "12,3 s"
    assert text.duration(125) == "2 min 5 s"
    assert text.duration(3700) == "1 h 1 min"
    assert text.skew(0.3) == "relógio +0,3 s"
    assert text.skew(-2) == "relógio −2,0 s"
    assert text.fold("Memória ÇÃO") == "memoria cao"
    assert text.state("tempo_esgotado") == "Tempo esgotado"


def test_friendly_errors():
    from relay.core.errors import AuthError, ConnectionFailed, RemoteError

    assert friendly_error(ConnectionFailed("caiu"), "g15") == "Não deu pra falar com g15. caiu"
    assert "Confira o pareamento" in friendly_error(AuthError("x"), "g15")
    assert friendly_error(RemoteError("não existe"), "g15") == "g15 recusou: não existe"
    assert friendly_error(ValueError("x")).startswith("Erro inesperado")


def test_gui_logic_never_imports_gi():
    import subprocess
    import sys

    code = (
        "import sys; import relay.gui.viewmodels, relay.gui.services, relay.gui.form, relay.gui.history; "
        "sys.exit(1 if 'gi' in sys.modules else 0)"
    )
    env = {**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "..", "src")}
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0


# --------------------------------------------------------------------------
# pareamento pela interface


def test_pairing_view_models(tmp_path):
    from relay.core import config as config_mod
    from relay.gui.viewmodels import PairAgentVM, PairControllerVM

    loop = FakeLoop()
    agent_path = tmp_path / "agente.json"
    cfg = config_mod.default_config("agente")
    cfg.agent.listen = "127.0.0.1"
    cfg.agent.allowed = ["127.0.0.0/8"]
    cfg.agent.verify_tailscale = "nunca"
    cfg.agent.pairing_port = 0
    config_mod.save(cfg, agent_path)
    ctl_path = tmp_path / "controle.json"
    config_mod.save(config_mod.default_config("controle"), ctl_path)

    agent_vm = PairAgentVM(loop.dispatch, SettingsVM(agent_path))
    agent_vm.start()
    assert agent_vm.state == PairAgentVM.WAITING
    assert len(agent_vm.code.replace(" ", "")) == 8 and 0 < agent_vm.remaining() <= 120
    port = agent_vm.window.port

    service = ControllerService(loop.dispatch)
    ctl_vm = PairControllerVM(service, SettingsVM(ctl_path))
    ctl_vm.start("", port, agent_vm.code)
    assert "Digite o nome" in ctl_vm.error
    ctl_vm.start("127.0.0.1", port, "12")
    assert "8 números" in ctl_vm.error
    ctl_vm.start("127.0.0.1", port, agent_vm.code)
    assert ctl_vm.busy
    loop.pump(lambda: not ctl_vm.busy and agent_vm.state != PairAgentVM.WAITING)
    assert ctl_vm.paired == "agente", ctl_vm.error
    assert agent_vm.state == PairAgentVM.PAIRED and agent_vm.controller == "controle"
    a = config_mod.load(agent_path)
    c = config_mod.load(ctl_path)
    assert a.agent.controllers["controle"].secret == c.machines["agente"].secret
    service.shutdown()


def test_pairing_agent_vm_without_tailscale(tmp_path):
    from relay.core import config as config_mod
    from relay.gui.viewmodels import PairAgentVM

    path = tmp_path / "a.json"
    config_mod.save(config_mod.default_config("a"), path)
    vm = PairAgentVM(FakeLoop().dispatch, SettingsVM(path), find_ip=lambda: None)
    vm.start()
    assert vm.state == PairAgentVM.FAILED and "Tailscale" in vm.message
