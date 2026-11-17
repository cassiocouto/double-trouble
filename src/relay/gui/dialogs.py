"""Configurações: máquinas que esta controla, esta máquina e quem pode controlar ela."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core.config import DEFAULT_PORT  # noqa: E402
from .viewmodels import SettingsVM  # noqa: E402
from .widgets import button, label, row, toast, vbox  # noqa: E402

if TYPE_CHECKING:
    from .window import MainWindow


class PreferencesDialog:
    def __init__(self, win: MainWindow, vm: SettingsVM):
        self.win = win
        self.vm = vm
        self.dialog = Adw.PreferencesDialog()
        self.dialog.set_title("Configurações")

        self.machines_page = Adw.PreferencesPage()
        self.machines_page.set_title("Máquinas")
        self.machines_page.set_icon_name("network-workgroup-symbolic")
        self.machines_group = Adw.PreferencesGroup()
        self.machines_group.set_title("Máquinas que esta controla")
        self.machines_group.set_description("Pra quem esta máquina manda comandos.")
        add_box = Gtk.Box(spacing=6)
        pair = button("Digitar código", "suggested-action")
        pair.connect("clicked", lambda *_: self.win.show_pairing_controller(self))
        manual = button("Com segredo", "flat")
        manual.set_tooltip_text("Caminho manual: colar o segredo gerado na outra máquina")
        manual.connect("clicked", lambda *_: self._machine_dialog())
        add_box.append(pair)
        add_box.append(manual)
        self.machines_group.set_header_suffix(add_box)
        self.machines_page.add(self.machines_group)
        self.dialog.add(self.machines_page)

        self.agent_page = Adw.PreferencesPage()
        self.agent_page.set_title("Esta máquina")
        self.agent_page.set_icon_name("computer-symbolic")
        self.this_group = Adw.PreferencesGroup()
        self.this_group.set_title("Esta máquina")
        self.this_group.set_description("As mudanças valem na hora: o agente relê o config sozinho.")
        self.agent_page.add(self.this_group)
        self.ctrl_group = Adw.PreferencesGroup()
        self.ctrl_group.set_title("Quem pode controlar esta máquina")
        let = button("Mostrar código", "suggested-action")
        let.set_tooltip_text("Mostra um código curto pra digitar na outra máquina")
        let.connect("clicked", lambda *_: self.win.show_pairing_agent(self))
        self.ctrl_group.set_header_suffix(let)
        self.agent_page.add(self.ctrl_group)
        self.dialog.add(self.agent_page)

        self._build_claude_page()

        self._rows: list[tuple[Adw.PreferencesGroup, Gtk.Widget]] = []
        vm.changed.connect(self.rebuild)
        self.rebuild()

    def present(self) -> None:
        self.dialog.present(self.win)

    def toast(self, msg: str) -> None:
        self.dialog.add_toast(toast(msg))

    def _build_claude_page(self) -> None:
        from ..core import vision

        page = Adw.PreferencesPage()
        page.set_title("Claude (OCR)")
        page.set_icon_name("applets-screenshooter-symbolic")
        group = Adw.PreferencesGroup()
        group.set_title("Ler texto da tela")
        group.set_description(
            "A tela de 'Ler tela' usa a API do Claude pra extrair o texto de um recorte. "
            "A chave fica só nesta máquina, num arquivo com permissão restrita."
        )
        try:
            current = vision.load_settings()
        except Exception:  # config quebrado: começa em branco
            current = vision.VisionSettings()
        self._claude_key = Adw.PasswordEntryRow()
        self._claude_key.set_title("Chave da API (sk-ant-…)")
        self._claude_key.set_text(current.api_key)
        self._claude_model = Adw.EntryRow()
        self._claude_model.set_use_markup(False)
        self._claude_model.set_title("Modelo")
        self._claude_model.set_text(current.model)
        group.add(self._claude_key)
        group.add(self._claude_model)
        save = button("Salvar chave", "pill", "suggested-action")
        save.set_halign(Gtk.Align.CENTER)
        save.set_margin_top(12)
        save.connect("clicked", self._save_claude)
        group.add(save)
        page.add(group)
        self.dialog.add(page)

    def _save_claude(self, *_):
        from ..core import vision

        key = self._claude_key.get_text().strip()
        model = self._claude_model.get_text().strip() or vision.DEFAULT_MODEL
        try:
            vision.save_settings(vision.VisionSettings(api_key=key, model=model))
        except Exception as e:
            self.toast(f"Não consegui salvar ({e})")
            return
        self.toast("Chave salva" if key else "Chave removida")

    def _clear(self) -> None:
        for group, w in self._rows:
            group.remove(w)
        self._rows = []

    def _add(self, group: Adw.PreferencesGroup, w: Gtk.Widget) -> None:
        group.add(w)
        self._rows.append((group, w))

    def rebuild(self) -> None:
        self._clear()
        cfg = self.vm.load()
        if cfg is None:
            self._add(self.machines_group, label(self.vm.error, "error"))
            self._add(self.this_group, label("Rode 'relay init' num terminal pra criar o config.", "dim-label"))
            return
        if not cfg.machines:
            self._add(
                self.machines_group,
                label("Nenhuma ainda. Pareie com “Digitar código” ou “Mostrar código”.", "dim-label"),
            )
        for m in cfg.machines.values():
            r = row(m.name, f"{m.address}:{m.port}")
            edit = button("", "flat", icon="document-edit-symbolic", tooltip="Mudar endereço ou porta")
            edit.set_valign(Gtk.Align.CENTER)
            edit.connect("clicked", lambda _b, name=m.name: self._machine_dialog(name))
            rm = button("", "flat", icon="user-trash-symbolic", tooltip="Esquecer esta máquina")
            rm.set_valign(Gtk.Align.CENTER)
            rm.connect("clicked", lambda _b, name=m.name: self._confirm_remove(name))
            r.add_suffix(edit)
            r.add_suffix(rm)
            self._add(self.machines_group, r)

        a = cfg.agent
        self._add(self.this_group, row("Nome", cfg.name))
        listen = "o IP do Tailscale" if a.listen == "tailscale" else a.listen
        self._add(self.this_group, row("Escuta em", f"{listen}, porta {a.port}"))
        self._add(self.this_group, row("Pareamento", f"porta {a.pairing_port}"))
        self._add(self.this_group, row("Comandos em paralelo", str(a.max_parallel)))
        if not a.controllers:
            self._add(
                self.ctrl_group, label("Ninguém ainda. Pareie com “Mostrar código” ou “Digitar código”.", "dim-label")
            )
        for c in a.controllers.values():
            r = row(c.name, self.vm.controller_summary(c))
            perms = button("", "flat", icon="emblem-system-symbolic", tooltip="Escolher quais comandos ela pode rodar")
            perms.set_valign(Gtk.Align.CENTER)
            perms.connect("clicked", lambda _b, name=c.name: self._permissions_dialog(name))
            rm = button("", "flat", icon="user-trash-symbolic", tooltip="Tirar a permissão")
            rm.set_valign(Gtk.Align.CENTER)
            rm.connect("clicked", lambda _b, name=c.name: self._confirm_revoke(name))
            r.add_suffix(perms)
            r.add_suffix(rm)
            self._add(self.ctrl_group, r)

    # -- diálogos ---------------------------------------------------------
    def _alert(self, heading: str, body: str, action: str, on_yes) -> None:
        d = Adw.AlertDialog.new(heading, body)
        d.add_response("nao", "Cancelar")
        d.add_response("sim", action)
        d.set_response_appearance("sim", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("nao")
        d.connect("response", lambda _d, resp: on_yes() if resp == "sim" else None)
        d.present(self.dialog)

    def _report(self, error: str | None, ok: str) -> None:
        self.toast(error or ok)

    def _confirm_remove(self, name: str) -> None:
        self._alert(
            f"Esquecer {name}?",
            "Esta máquina para de mandar comandos pra ela. Pra voltar, é só parear de novo.",
            "Esquecer",
            lambda: self._report(self.vm.remove_machine(name), f"{name} esquecida"),
        )

    def _confirm_revoke(self, name: str) -> None:
        self._alert(
            f"Tirar a permissão de {name}?",
            f"{name} não vai mais conseguir rodar nada aqui. Vale na hora.",
            "Tirar",
            lambda: self._report(self.vm.revoke_controller(name), f"{name} não controla mais esta máquina"),
        )

    def _machine_dialog(self, existing: str | None = None) -> None:
        cfg = self.vm.load()
        current = cfg.machines.get(existing) if (cfg and existing) else None
        d = Adw.Dialog()
        d.set_title(f"Editar {existing}" if existing else "Adicionar com segredo")
        d.set_content_width(460)
        box = vbox(12)
        group = Adw.PreferencesGroup()
        name = Adw.EntryRow()
        name.set_title("Nome da máquina")
        name.set_text(existing or "")
        name.set_sensitive(existing is None)
        address = Adw.EntryRow()
        address.set_title("Endereço (vazio = o nome, pelo MagicDNS)")
        address.set_text(current.address if current else "")
        port = Adw.SpinRow.new_with_range(1, 65535, 1)
        port.set_title("Porta")
        port.set_value(current.port if current else DEFAULT_PORT)
        group.add(name)
        group.add(address)
        group.add(port)
        secret = Adw.PasswordEntryRow()
        if existing is None:
            secret.set_title("Segredo (hex, gerado com 'relay agente autorizar' na outra)")
            group.add(secret)
        box.append(group)
        error = label("", "error")
        error.set_visible(False)
        box.append(error)
        save = button("Salvar", "pill", "suggested-action")
        save.set_halign(Gtk.Align.CENTER)
        box.append(save)

        def on_save(*_):
            if existing is None:
                err = self.vm.add_machine(name.get_text(), address.get_text(), port.get_value(), secret.get_text())
            else:
                err = self.vm.edit_machine(existing, address.get_text(), port.get_value())
            if err:
                error.set_label(err)
                error.set_visible(True)
                return
            d.close()
            self.toast("Salvo")

        save.connect("clicked", on_save)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        d.set_child(view)
        d.present(self.dialog)

    def _permissions_dialog(self, controller: str) -> None:
        cfg = self.vm.load()
        if cfg is None or controller not in cfg.agent.controllers:
            return
        entry = cfg.agent.controllers[controller]
        from ..core import commands

        local = [c["nome"] for c in commands.list_commands()]
        d = Adw.Dialog()
        d.set_title(f"O que {controller} pode rodar")
        d.set_content_width(460)
        d.set_content_height(560)
        box = vbox(12)
        everything = Adw.SwitchRow()
        everything.set_title("Todos os comandos")
        everything.set_subtitle("Inclui comandos que você criar depois")
        everything.set_active(entry.commands is None)
        g_all = Adw.PreferencesGroup()
        g_all.add(everything)
        box.append(g_all)
        g_each = Adw.PreferencesGroup()
        g_each.set_title("Só estes")
        switches: dict[str, Adw.SwitchRow] = {}
        allowed = set(entry.commands or [])
        for name in sorted(set(local) | allowed):
            s = Adw.SwitchRow()
            s.set_use_markup(False)
            s.set_title(name)
            s.set_active(name in allowed)
            g_each.add(s)
            switches[name] = s
        box.append(g_each)

        def sync(*_):
            g_each.set_sensitive(not everything.get_active())

        everything.connect("notify::active", sync)
        sync()
        save = button("Salvar", "pill", "suggested-action")
        save.set_halign(Gtk.Align.CENTER)
        box.append(save)

        def on_save(*_):
            chosen = None if everything.get_active() else [n for n, s in switches.items() if s.get_active()]
            err = self.vm.set_controller_commands(controller, chosen)
            if err:
                self.toast(err)
                return
            d.close()
            self.toast("Permissões salvas. Valem na hora")

        save.connect("clicked", on_save)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_child(box)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(sw)
        d.set_child(view)
        d.present(self.dialog)


def info_markup(text: str) -> str:
    return GLib.markup_escape_text(text)


class PairControllerDialog:
    """Esta máquina vai mandar: digitar o código da outra."""

    def __init__(self, parent: Gtk.Widget, vm):
        from ..core.config import DEFAULT_PORT as PORT

        self.vm = vm
        self.parent = parent
        d = Adw.Dialog()
        d.set_title("Digitar o código da outra máquina")
        d.set_content_width(460)
        self.dialog = d
        box = vbox(12)
        box.append(
            label(
                "Na outra máquina, abra Configurações → Esta máquina → “Mostrar código”, "
                "ou rode relay parear. Ela mostra 8 números. Depois do pareamento, "
                "as duas máquinas mandam uma na outra.",
                "dim-label",
            )
        )
        group = Adw.PreferencesGroup()
        self.address = Adw.EntryRow()
        self.address.set_title("Nome da outra máquina (pode ser só um pedaço, como g15)")
        self.port = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port.set_title("Porta de pareamento")
        self.port.set_value(PORT + 1)
        self.code = Adw.EntryRow()
        self.code.set_title("Código")
        group.add(self.address)
        group.add(self.port)
        group.add(self.code)
        box.append(group)
        self.status = label("", "caption")
        box.append(self.status)
        self.go = button("Parear", "pill", "suggested-action")
        self.go.set_halign(Gtk.Align.CENTER)
        self.go.connect("clicked", self._clicked)
        box.append(self.go)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        d.set_child(view)
        vm.changed.connect(self.refresh)

    def present(self) -> None:
        self.dialog.present(self.parent)

    def _clicked(self, *_):
        if self.vm.paired:
            self.dialog.close()
            return
        self.vm.start(self.address.get_text(), self.port.get_value(), self.code.get_text())

    def refresh(self) -> None:
        vm = self.vm
        self.go.set_sensitive(not vm.busy)
        for c in ("error", "success"):
            self.status.remove_css_class(c)
        if vm.busy:
            self.status.set_label("Pareando…")
        elif vm.error:
            self.status.set_label(vm.error)
            self.status.add_css_class("error")
        elif vm.paired:
            self.status.set_label(f"Pronto! Esta máquina e {vm.paired} agora mandam uma na outra.")
            self.status.add_css_class("success")
            self.go.set_label("Fechar")


class PairAgentDialog:
    """Esta máquina vai executar: mostra o código e espera a outra."""

    def __init__(self, parent: Gtk.Widget, vm):
        self.vm = vm
        self.parent = parent
        self._timer = 0
        d = Adw.Dialog()
        d.set_title("Mostrar código pra parear")
        d.set_content_width(460)
        d.connect("closed", lambda *_: self._closed())
        self.dialog = d
        box = vbox(12)
        self.code = label("", "title-1", wrap=False, selectable=True)
        self.code.set_halign(Gtk.Align.CENTER)
        self.countdown = label("", "dim-label")
        self.countdown.set_halign(Gtk.Align.CENTER)
        self.message = label("")
        self.log = label("", "dim-label", "caption")
        box.append(self.code)
        box.append(self.countdown)
        box.append(self.message)
        box.append(self.log)
        self.again = button("Novo código", "pill")
        self.again.set_halign(Gtk.Align.CENTER)
        self.again.connect("clicked", lambda *_: self._start())
        box.append(self.again)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        d.set_child(view)
        vm.changed.connect(self.refresh)

    def present(self) -> None:
        self.dialog.present(self.parent)
        self._start()

    def _start(self) -> None:
        self.vm.log.clear()
        self.vm.start()
        if self._timer == 0:
            self._timer = GLib.timeout_add(500, self._tick)

    def _tick(self) -> bool:
        if self.vm.state != self.vm.WAITING:
            self._timer = 0
            self.refresh()
            return False
        self.countdown.set_label(f"fecha em {self.vm.remaining()} s")
        return True

    def _closed(self) -> None:
        self.vm.stop()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def refresh(self) -> None:
        vm = self.vm
        waiting = vm.state == vm.WAITING
        self.code.set_label(vm.code if waiting else ("✓" if vm.state == vm.PAIRED else "—"))
        self.countdown.set_visible(waiting)
        self.message.set_label(vm.message)
        for c in ("error", "success"):
            self.message.remove_css_class(c)
        if vm.state == vm.PAIRED:
            self.message.add_css_class("success")
        elif vm.state in (vm.FAILED, vm.CLOSED):
            self.message.add_css_class("error")
        self.log.set_label("\n".join(vm.log[-4:]))
        self.again.set_visible(vm.state in (vm.CLOSED, vm.FAILED))
