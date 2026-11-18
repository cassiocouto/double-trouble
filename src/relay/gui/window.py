"""Janela principal: uma pilha de páginas (máquinas → comandos → executar)."""

from __future__ import annotations

import contextlib
import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID  # noqa: E402
from ..core import config as config_mod  # noqa: E402
from ..core import paths  # noqa: E402
from .history import Entry, HistoryStore  # noqa: E402

DEFAULT_SIZE = (1040, 860)
from .pages import CommandsPage, HistoryEntryPage, HistoryPage, MachinesPage, RunPage  # noqa: E402
from .services import ControllerService  # noqa: E402
from .viewmodels import (  # noqa: E402
    CommandsVM,
    HistoryVM,
    MachinesVM,
    PairAgentVM,
    PairControllerVM,
    RunVM,
    SettingsVM,
)
from .widgets import toast  # noqa: E402

REFRESH_SECONDS = 15


def glib_dispatch(fn, *args) -> None:
    """Roda fn(*args) na thread da tela."""

    def call() -> bool:
        fn(*args)
        return False

    GLib.idle_add(call)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.app = app
        self.set_title("Relay")
        w, h = self._load_size()
        self.set_default_size(w, h)
        self.set_icon_name(APP_ID)

        self.service = ControllerService(glib_dispatch, load_config=config_mod.load)
        self.history = HistoryStore()
        self.settings = SettingsVM()
        self.machines = MachinesVM(self.service)
        self.history_vm = HistoryVM(self.history)

        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)

        self.machines_page = MachinesPage(self, self.machines)
        self.nav.add(self.machines_page.page)
        self._pages: list[object] = []  # mantém vivas as páginas empilhadas

        self.settings.changed.connect(self._config_changed)
        self.machines.reload()
        self.machines.refresh()
        self._timer = GLib.timeout_add_seconds(REFRESH_SECONDS, self._auto_refresh)
        self.connect("close-request", self._on_close)

    # -- tamanho da janela --------------------------------------------------
    def _size_file(self):
        return paths.base_dir() / "janela.json"

    def _load_size(self) -> tuple[int, int]:
        try:
            data = json.loads(self._size_file().read_text())
            return max(640, int(data["largura"])), max(560, int(data["altura"]))
        except Exception:
            return DEFAULT_SIZE

    def _save_size(self) -> None:
        with contextlib.suppress(Exception):
            w, h = self.get_width(), self.get_height()
            if w < 200 or h < 200:  # minimizada ou ainda sem alocação
                return
            paths.ensure_private_dir(paths.base_dir())
            self._size_file().write_text(json.dumps({"largura": w, "altura": h}))

    # -- navegação ----------------------------------------------------------
    def app_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append("Histórico", "app.historico")
        menu.append("Configurações", "app.configuracoes")
        menu.append("Sobre o Relay", "app.sobre")
        return menu

    def _push(self, holder) -> None:
        self._pages.append(holder)
        if len(self._pages) > 20:
            self._pages = self._pages[-20:]
        self.nav.push(holder.page)

    def show_commands(self, machine: str) -> CommandsVM:
        vm = CommandsVM(self.service, machine)
        self._push(CommandsPage(self, vm))
        vm.load()
        return vm

    def show_run(self, machine: str, command: dict, previous: dict | None = None) -> None:
        vm = RunVM(
            self.service,
            machine,
            command,
            history=self.history,
            notifier=self.notify_desktop,
            is_focused=self.is_active,
            previous_values=previous,
        )
        vm.changed.connect(lambda: self.history_vm.reload() if vm.state in (RunVM.DONE, RunVM.FAILED) else None)
        self._push(RunPage(self, vm))

    def show_capture(self, machine: str, prefill: bytes | None = None) -> None:
        from .pages import CapturePage

        self._push(CapturePage(self, machine, prefill=prefill))

    def make_extractor(self):
        """Função que a tela de captura usa pra ler o recorte com a API do Claude.

        Lê a chave na hora de cada chamada, pra uma chave recém-salva valer.
        """
        from ..core import vision

        def extract(image_png: bytes, mode: str, question):
            return vision.extract_text(image_png, mode=mode, question=question, settings=vision.load_settings())

        return extract

    def save_text(self, text: str, suggested_name: str = "texto") -> None:
        dialog = Gtk.FileDialog()
        stamp = GLib.DateTime.new_now_local().format("%Y%m%d-%H%M%S")
        dialog.set_initial_name(f"{suggested_name}-{stamp}.txt")

        def done(dlg, result):
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return
            try:
                Gio.File.replace_contents(gfile, text.encode("utf-8"), None, False, Gio.FileCreateFlags.NONE, None)
                self.toast("Texto salvo")
            except GLib.Error as e:
                self.toast(f"Não consegui salvar ({e.message})")

        dialog.save(self, None, done)

    def show_history(self) -> None:
        self.history_vm.reload()
        self._push(HistoryPage(self, self.history_vm))

    def show_history_entry(self, entry: Entry) -> None:
        self._push(HistoryEntryPage(self, entry))

    def rerun(self, entry: Entry) -> None:
        """Busca a lista de comandos de novo (pode ter mudado) e abre o formulário preenchido."""

        def found(items: list[dict]) -> None:
            cmd = next((c for c in items if c.get("nome") == entry.command), None)
            if cmd is None:
                self.toast(f"{entry.machine} não tem mais o comando {entry.command}")
                return
            self.show_run(entry.machine, cmd, previous=entry.values)

        self.service.list_commands(entry.machine, found, self.toast)

    def show_preferences(self) -> None:
        from .dialogs import PreferencesDialog

        self._prefs = PreferencesDialog(self, self.settings)
        self._prefs.present()

    def show_pairing_controller(self, prefs) -> None:
        from .dialogs import PairControllerDialog

        self._pair_dialog = PairControllerDialog(prefs.dialog, PairControllerVM(self.service, self.settings))
        self._pair_dialog.present()

    def show_pairing_agent(self, prefs) -> None:
        from .dialogs import PairAgentDialog

        self._pair_dialog = PairAgentDialog(prefs.dialog, PairAgentVM(glib_dispatch, self.settings))
        self._pair_dialog.present()

    # -- avisos -------------------------------------------------------------
    def toast(self, msg: str) -> None:
        self.toasts.add_toast(toast(msg))

    def notify_desktop(self, title: str, body: str) -> None:
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_default_action("app.mostrar")
        self.app.send_notification("relay-job", n)

    # -- ciclo --------------------------------------------------------------
    def _config_changed(self) -> None:
        self.machines.reload()
        self.machines.refresh()

    def _auto_refresh(self) -> bool:
        if self.is_visible():
            self.machines.refresh()
        return True

    def _on_close(self, *_args) -> bool:
        self._save_size()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.service.shutdown()
        self.history.close()
        return False


def build_about(win: Gtk.Window) -> Adw.AboutDialog:
    from .. import __version__

    about = Adw.AboutDialog()
    about.set_application_name("Relay")
    about.set_application_icon(APP_ID)
    about.set_version(__version__)
    about.set_developer_name("Cass")
    about.set_comments("Uma máquina roda comandos pré-aprovados na outra, pelo Tailscale.")
    about.set_license_type(Gtk.License.MIT_X11)
    return about
