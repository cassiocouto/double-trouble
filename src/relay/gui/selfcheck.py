"""relay-gui --checar: monta todas as telas e lista os erros, sem mostrar janela.

Serve pra achar de uma vez os problemas de uma versão nova do GTK ou da
libadwaita. Não manda nada pra rede de propósito (a tela de máquinas faz o
ping normal de sempre), não abre o pareamento e não mexe na área de
transferência.
"""

from __future__ import annotations

import base64
import time
import traceback
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject  # noqa: E402

from .history import Entry  # noqa: E402
from .pages import CapturePage, CommandsPage, HistoryEntryPage, HistoryPage, RunPage  # noqa: E402
from .viewmodels import CommandsVM, PairAgentVM, PairControllerVM, RunVM  # noqa: E402

# PNG 1x1 de verdade, pra o Gdk.Texture conseguir carregar na checagem.
SAMPLE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SAMPLE_COMMANDS = [
    {"nome": "status", "descricao": "Mostra memória e disco.", "categoria": "Sistema", "icone": "computer-symbolic"},
    {
        "nome": "tudo",
        "descricao": "Um campo de cada tipo.",
        "detalhes": "Texto com <marcação> & símbolos, que não pode virar markup.",
        "categoria": "Tela",
        "confirmar": True,
        "timeout": 15,
        "avisos": ["aviso de exemplo"],
        "parametros": [
            {"nome": "texto", "tipo": "texto", "rotulo": "Mensagem", "obrigatorio": True, "max": 50},
            {"nome": "senha", "tipo": "texto", "segredo": True},
            {"nome": "nota", "tipo": "texto", "multilinha": True},
            {"nome": "vezes", "tipo": "inteiro", "min": 1, "max": 10, "padrao": 2},
            {"nome": "talvez", "tipo": "inteiro"},
            {"nome": "urgente", "tipo": "booleano", "ajuda": "Fica na tela"},
            {"nome": "modo", "tipo": "escolha", "opcoes": ["a", "b"]},
        ],
    },
    {"nome": "antigo", "descricao": "Aceita argumentos soltos.", "aceita_args": True},
    {"nome": "quebrado", "bloqueado": "tem erro de sintaxe na linha 2"},
]


def run_checks(win) -> list[tuple[str, str | None]]:
    """Roda cada passo e devolve (nome, erro ou None)."""
    results: list[tuple[str, str | None]] = []

    def step(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            results.append((name, None))
        except Exception:
            results.append((name, traceback.format_exc()))

    def machines() -> None:
        vm = win.machines
        vm.config_error = "config de exemplo com <problema>"
        vm.changed.emit()
        vm.config_error = ""
        vm.changed.emit()
        for state in vm.machines.values():
            state.status, state.latency_ms, state.skew, state.version = "online", 12.0, 45.0, "2.0.0"
            vm.machine_changed.emit(state.name)
            state.status, state.error = "offline", "não respondeu"
            vm.machine_changed.emit(state.name)

    def commands() -> None:
        vm = CommandsVM(win.service, "maquina-de-teste")
        CommandsPage(win, vm)
        vm.loading = True
        vm.changed.emit()
        vm.loading, vm.commands = False, list(SAMPLE_COMMANDS)
        vm.changed.emit()
        vm.set_query("tela")
        vm.set_query("nada que exista")
        vm.error = "não deu pra falar com <x>"
        vm.changed.emit()

    def run_page(cmd: dict) -> Callable[[], None]:
        def check() -> None:
            vm = RunVM(win.service, "maquina-de-teste", cmd)
            page = RunPage(win, vm)
            values = page.form.values()
            if page.form.form.fields:
                page.form.show_errors({page.form.form.fields[0].name: "erro de exemplo"})
                page.form.show_errors({})
            assert isinstance(values, dict)
            page.output.append("stdout", "saída <normal>\n")
            page.output.append("stderr", "erro & aviso\n")
            vm.state, vm.started_at = RunVM.RUNNING, vm.clock()
            vm.changed.emit()
            page._tick()
            page._stop_ticker()
            vm.state, vm.ended_at = RunVM.DONE, time.monotonic()
            for estado in ("ok", "falhou", "cancelado", "tempo_esgotado", "erro"):
                vm.result = {"estado": estado, "codigo": 3, "duracao": 1.5, "timeout": 15, "erro": "x"}
                vm.message = vm.result_text()
                vm.changed.emit()
            vm.state, vm.message = RunVM.FAILED, "falhou de <exemplo>"
            vm.changed.emit()
            # Mostra uma imagem de verdade (1x1 PNG), alterna o zoom, e um
            # arquivo não-imagem.
            page._show_artifact(SAMPLE_PNG, True)
            page.image.zoom_btn.set_active(True)
            page.image.zoom_btn.set_active(False)
            page._show_artifact(b"%PDF-1.4 arquivo de exemplo", False)

        return check

    def capture() -> None:
        win.make_extractor = lambda: lambda img, mode, q: "texto de teste"
        page = CapturePage(win, "maquina-de-teste")
        page.crop.set_image(SAMPLE_PNG)
        page.crop._start, page.crop._cur = (1.0, 1.0), (40.0, 30.0)
        page.crop.crop_png()  # recorte (ou imagem inteira, se pequeno demais)
        page.mode.set_selected(1)
        page._mode_changed()
        page.vm._append("texto de exemplo <com> & símbolos")
        page.vm._append("segundo recorte")
        page._refresh()
        assert page.vm.snippets == 2

    def confirm_dialog() -> None:
        d = Adw.AlertDialog.new("Rodar “tudo” em maquina-de-teste?", "Este comando pede confirmação.")
        d.add_response("nao", "Não rodar")
        d.add_response("sim", "Rodar")
        d.set_response_appearance("sim", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("nao")
        d.set_close_response("nao")

    def history() -> None:
        entry = Entry(
            id=1,
            machine="maquina-de-teste",
            command="tudo",
            values={"texto": "oi", "args": ["a", "b"]},
            job="abc123",
            started=time.time(),
            duration=2.5,
            state="ok",
            code=0,
            stdout="saída\n",
            stderr="erro\n",
            truncated=True,
            error=None,
        )
        win.history_vm.entries = [entry]
        HistoryPage(win, win.history_vm)
        win.history_vm.changed.emit()
        win.history_vm.entries = []
        win.history_vm.changed.emit()
        HistoryEntryPage(win, entry)

    def preferences() -> None:
        from .dialogs import PreferencesDialog

        prefs = PreferencesDialog(win, win.settings)
        prefs.rebuild()
        prefs.toast("teste")

    def pairing_dialogs() -> None:
        from .dialogs import PairAgentDialog, PairControllerDialog

        cvm = PairControllerVM(win.service, win.settings)
        cdlg = PairControllerDialog(win, cvm)
        for busy, error, paired in ((True, "", ""), (False, "código errado", ""), (False, "", "outra")):
            cvm.busy, cvm.error, cvm.paired = busy, error, paired
            cdlg.refresh()
        avm = PairAgentVM(lambda fn, *a: fn(*a), win.settings)
        adlg = PairAgentDialog(win, avm)
        for state in (avm.WAITING, avm.PAIRED, avm.CLOSED, avm.FAILED):
            avm.state, avm.code, avm.message = state, "1234 5678", f"estado {state}"
            avm.log = ["linha 1", "linha 2"]
            adlg.refresh()

    def misc() -> None:
        from .window import build_about

        build_about(win)
        win.toast("toast de exemplo com <sinal> & coisa")
        n = Gio.Notification.new("título")
        n.set_body("corpo")
        n.set_default_action("app.mostrar")
        Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_STRING, "x"))
        assert hasattr(win.get_clipboard(), "set_content")

    step("tela de máquinas", machines)
    step("tela de comandos", commands)
    for cmd in SAMPLE_COMMANDS:
        step(f"tela de executar ({cmd['nome']})", run_page(cmd))
    step("ler tela (OCR)", capture)
    step("diálogo de confirmação", confirm_dialog)
    step("histórico", history)
    step("configurações", preferences)
    step("diálogos de pareamento", pairing_dialogs)
    step("sobre, aviso, notificação e área de transferência", misc)
    return results


def report(results: list[tuple[str, str | None]]) -> int:
    failed = [r for r in results if r[1]]
    for name, error in results:
        print(f"{'ok  ' if error is None else 'ERRO'}  {name}", flush=True)
    for name, error in failed:
        print(f"\n===== {name}\n{error}", flush=True)
    print(f"\n{len(results) - len(failed)} de {len(results)} telas ok", flush=True)
    return 1 if failed else 0
