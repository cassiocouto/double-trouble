"""As telas: máquinas, comandos, executar, histórico e detalhe do histórico.

Cada tela só desenha o view-model e repassa cliques. Nenhuma regra mora aqui.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from ..core.params import MAX_MULTILINE  # noqa: E402
from . import text  # noqa: E402
from .history import Entry  # noqa: E402
from .viewmodels import CaptureVM, CommandsVM, HistoryVM, MachinesVM, RunVM  # noqa: E402
from .widgets import (  # noqa: E402
    DEFAULT_ICON,
    CropView,
    FormView,
    ImageView,
    OutputView,
    button,
    copy_to_clipboard,
    label,
    page,
    row,
    scrolled,
    vbox,
)

if TYPE_CHECKING:
    from .window import MainWindow


# ==========================================================================
# Máquinas


class MachinesPage:
    def __init__(self, win: MainWindow, vm: MachinesVM):
        self.win = win
        self.vm = vm
        self.rows: dict[str, tuple[Adw.ActionRow, Gtk.Image, Gtk.Spinner]] = {}

        refresh = button("", "flat", icon="view-refresh-symbolic", tooltip="Ver de novo quem está online")
        refresh.connect("clicked", lambda *_: self.vm.refresh())
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=win.app_menu())
        menu.set_tooltip_text("Menu")
        history = button("", "flat", icon="document-open-recent-symbolic", tooltip="Histórico")
        history.connect("clicked", lambda *_: self.win.show_history())

        self.banner = Adw.Banner()
        self.banner.set_button_label("Configurações")
        self.banner.connect("button-clicked", lambda *_: self.win.show_preferences())

        self.list_box = vbox()
        self.group = Adw.PreferencesGroup()
        self.group.set_title("Máquinas")
        self.group.set_description("Clique numa máquina pra ver os comandos que ela aceita.")
        self.list_box.append(self.group)

        self.empty = Adw.StatusPage()
        self.empty.set_icon_name("network-workgroup-symbolic")
        self.empty.set_title("Nenhuma máquina ainda")
        self.empty.set_description("Pareie com a outra máquina pra mandar comandos pra ela.")
        pair = button("Parear com uma máquina", "pill", "suggested-action")
        pair.set_halign(Gtk.Align.CENTER)
        pair.connect("clicked", lambda *_: self.win.show_preferences())
        self.empty.set_child(pair)

        self.stack = Gtk.Stack()
        self.stack.add_named(scrolled(self.list_box), "lista")
        self.stack.add_named(self.empty, "vazio")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(self.stack)
        self.page = page("Relay", content, header_end=[menu, history])
        self.page.header.pack_start(refresh)

        vm.changed.connect(self.rebuild)
        vm.machine_changed.connect(self.update_row)

    def rebuild(self) -> None:
        for r, _, _ in self.rows.values():
            self.group.remove(r)
        self.rows.clear()
        if self.vm.config_error:
            self.banner.set_title(text_for_banner(self.vm.config_error))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        if not self.vm.machines:
            self.stack.set_visible_child_name("vazio")
            return
        self.stack.set_visible_child_name("lista")
        for name, state in self.vm.machines.items():
            r = row(name, state.subtitle, activatable=True)
            icon = Gtk.Image.new_from_icon_name("network-wired-disconnected-symbolic")
            spinner = Gtk.Spinner()
            r.add_prefix(icon)
            r.add_suffix(spinner)
            r.connect("activated", lambda _r, n=name: self.win.show_commands(n))
            self.group.add(r)
            self.rows[name] = (r, icon, spinner)
            self.update_row(name)

    def update_row(self, name: str) -> None:
        state = self.vm.machines.get(name)
        if name not in self.rows or state is None:
            return
        r, icon, spinner = self.rows[name]
        r.set_subtitle(state.subtitle + (f"\n{state.warning}" if state.warning else ""))
        checking = state.status == "verificando"
        spinner.set_spinning(checking)
        spinner.set_visible(checking)
        if state.online:
            icon.set_from_icon_name("network-wired-symbolic")
            icon.remove_css_class("dim-label")
            icon.add_css_class("success")
        else:
            icon.set_from_icon_name("network-wired-disconnected-symbolic")
            icon.remove_css_class("success")
            icon.add_css_class("dim-label")
        if state.warning:
            r.add_css_class("warning")
        else:
            r.remove_css_class("warning")


def text_for_banner(msg: str) -> str:
    return GLib.markup_escape_text(msg if len(msg) < 140 else msg[:137] + "…")


# ==========================================================================
# Comandos de uma máquina


class CommandsPage:
    def __init__(self, win: MainWindow, vm: CommandsVM):
        self.win = win
        self.vm = vm
        self.groups: list[Adw.PreferencesGroup] = []

        search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        search_btn.set_tooltip_text("Buscar")
        reload_btn = button("", "flat", icon="view-refresh-symbolic", tooltip="Recarregar a lista")
        reload_btn.connect("clicked", lambda *_: self.vm.load())
        capture_btn = button("", "flat", icon="applets-screenshooter-symbolic", tooltip="Ler o texto da tela (OCR)")
        capture_btn.connect("clicked", lambda *_: self.win.show_capture(self.vm.machine))

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Buscar por nome, descrição ou categoria")
        self.search.connect("search-changed", lambda e: self.vm.set_query(e.get_text()))
        self.bar = Gtk.SearchBar()
        self.bar.set_child(self.search)
        self.bar.connect_entry(self.search)
        search_btn.bind_property(
            "active",
            self.bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        self.box = vbox()
        self.loading = Adw.StatusPage()
        self.loading.set_title("Carregando os comandos…")
        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.set_size_request(32, 32)
        self.loading.set_child(spinner)
        self.error = Adw.StatusPage()
        self.error.set_icon_name("dialog-error-symbolic")
        self.error.set_title("Não deu pra listar os comandos")
        retry = button("Tentar de novo", "pill")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self.vm.load())
        self.error.set_child(retry)
        self.nothing = Adw.StatusPage()
        self.nothing.set_icon_name("system-search-symbolic")
        self.nothing.set_title("Nada encontrado")

        self.stack = Gtk.Stack()
        self.stack.add_named(scrolled(self.box), "lista")
        self.stack.add_named(self.loading, "carregando")
        self.stack.add_named(self.error, "erro")
        self.stack.add_named(self.nothing, "nada")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.bar)
        content.append(self.stack)
        self.page = page(vm.machine, content, header_end=[search_btn, capture_btn, reload_btn])
        self.bar.set_key_capture_widget(self.page)
        vm.changed.connect(self.rebuild)

    def rebuild(self) -> None:
        if self.vm.loading:
            self.stack.set_visible_child_name("carregando")
            return
        if self.vm.error:
            self.error.set_description(GLib.markup_escape_text(self.vm.error))
            self.stack.set_visible_child_name("erro")
            return
        for g in self.groups:
            self.box.remove(g)
        self.groups = []
        groups = self.vm.groups()
        if not groups:
            self.nothing.set_description(
                "Esta máquina ainda não tem comandos." if not self.vm.query else "Tente outra palavra."
            )
            self.stack.set_visible_child_name("nada")
            return
        for category, items in groups:
            g = Adw.PreferencesGroup()
            g.set_title(GLib.markup_escape_text(category))
            for c in items:
                g.add(self._row(c))
            self.box.append(g)
            self.groups.append(g)
        self.stack.set_visible_child_name("lista")

    def _row(self, c: dict) -> Adw.ActionRow:
        blocked = c.get("bloqueado")
        subtitle = f"Bloqueado: {blocked}" if blocked else (c.get("descricao") or "(sem descrição)")
        r = row(c["nome"], subtitle, activatable=True, icon=c.get("icone") or DEFAULT_ICON)
        if c.get("confirmar"):
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn.set_tooltip_text("Pede confirmação antes de rodar")
            r.add_suffix(warn)
        if blocked:
            r.add_css_class("error")
        r.connect("activated", lambda *_: self.win.show_run(self.vm.machine, c))
        return r


# ==========================================================================
# Executar


class RunPage:
    def __init__(self, win: MainWindow, vm: RunVM):
        self.win = win
        self.vm = vm
        self._ticker: int | None = None
        box = vbox()
        c = vm.command

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title_row = Gtk.Box(spacing=8)
        title_row.append(label(c.get("descricao") or "(sem descrição)", "title-4"))
        if c.get("detalhes"):
            title_row.append(self._details_button(c["detalhes"]))
        head.append(title_row)
        facts = [f"em {vm.machine}", f"limite de {text.duration(c.get('timeout'))}"]
        if c.get("confirmar"):
            facts.append("pede confirmação")
        head.append(label(" · ".join(facts), "dim-label", "caption"))
        for w in c.get("avisos") or []:
            head.append(label(f"Aviso: {w}", "warning", "caption"))
        box.append(head)

        self.form = FormView(vm.form, vm.values)
        if not self.form.empty:
            box.append(self.form.group)

        actions = Gtk.Box(spacing=12)
        actions.set_halign(Gtk.Align.CENTER)
        self.run_btn = button("Rodar", "pill", "suggested-action")
        self.run_btn.connect("clicked", lambda *_: self._run())
        self.cancel_btn = button("Cancelar", "pill", "destructive-action")
        self.cancel_btn.connect("clicked", lambda *_: self.vm.cancel())
        actions.append(self.run_btn)
        actions.append(self.cancel_btn)
        box.append(actions)

        self.status = label("", "heading")
        self.job_label = label("", "dim-label", "caption", selectable=True)
        box.append(self.status)
        box.append(self.job_label)

        self.output = OutputView()
        out_head = Gtk.Box(spacing=6)
        out_head.append(label("Saída", "heading"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        out_head.append(spacer)
        copy = button("Copiar saída", "flat")
        copy.connect("clicked", self._copy)
        out_head.append(copy)
        box.append(out_head)
        box.append(self.output.frame)

        # Imagem (ou arquivo) que o comando devolver. Fica escondida até chegar uma.
        self.image = ImageView(self.win)
        self.image.container.set_visible(False)
        box.append(self.image.container)

        self.page = page(c["nome"], scrolled(box, max_width=1200))
        self.page.connect("hidden", lambda *_: self._stop_ticker())

        vm.changed.connect(self.refresh)
        vm.output_added.connect(self.output.append)
        vm.output_cleared.connect(self.output.clear)
        vm.errors_changed.connect(lambda: self.form.show_errors(self.vm.errors))
        vm.confirm_requested.connect(self._confirm)
        vm.artifact_ready.connect(self._show_artifact)
        for stream, chunk in vm.output:
            self.output.append(stream, chunk)
        if vm.artifact is not None:
            self._show_artifact(vm.artifact, vm.artifact_is_image)
        self.refresh()

    def _details_button(self, detalhes: str) -> Gtk.MenuButton:
        """Esconde o texto comprido de ajuda atrás de um (i), pra não poluir a tela."""
        info = Gtk.MenuButton(icon_name="help-about-symbolic")
        info.add_css_class("flat")
        info.set_valign(Gtk.Align.CENTER)
        info.set_tooltip_text("Detalhes")
        lbl = label(detalhes, "dim-label", "caption")
        lbl.set_max_width_chars(52)
        for side in ("top", "bottom", "start", "end"):
            getattr(lbl, f"set_margin_{side}")(12)
        pop = Gtk.Popover()
        pop.set_child(lbl)
        info.set_popover(pop)
        return info

    def _show_artifact(self, data: bytes, is_image: bool) -> None:
        self.image.container.set_visible(True)
        on_read = (lambda img: self.win.show_capture(self.vm.machine, prefill=img)) if is_image else None
        self.image.show(data, is_image, suggested_name=self.vm.command["nome"], on_read=on_read)

    def _run(self) -> None:
        values = self.form.values()
        if self.vm.command.get("nome") == "autodigitar":
            values = self._encrypt_autotype(values)
            if values is None:
                return
        self.vm.request_run(values)

    def _encrypt_autotype(self, values: dict) -> dict | None:
        """Cifra o texto do autodigitar aqui, pra quem roda pelo menu digitar aberto."""
        from ..core import autotype

        texto = values.get("texto", "")
        if not str(texto).strip():
            return values  # deixa o formulário reclamar do obrigatório
        try:
            cfg = autotype.load_settings()
            cifrado = autotype.encrypt(str(texto), cfg.senha)
        except Exception as e:
            self.win.toast(str(e))
            return None
        if len(cifrado) > MAX_MULTILINE:
            self.win.toast(f"o texto é grande demais (cifrado passa de {MAX_MULTILINE})")
            return None
        try:
            atraso = int(values.get("atraso", 3))
        except (TypeError, ValueError):
            atraso = 3
        eta = autotype.estimate_seconds(len(str(texto)), atraso=atraso, cps=cfg.cps)
        aviso = " (pode estourar o tempo, mande um bloco menor)" if eta > autotype.COMMAND_TIMEOUT - 30 else ""
        self.win.toast(f"vai digitar em uns {autotype.format_eta(eta)}{aviso}")
        return {**values, "texto": cifrado}

    def _copy(self, *_):
        copy_to_clipboard(self.page, self.vm.output_text())
        self.win.toast("Saída copiada")

    def _confirm(self, question: str) -> None:
        dialog = Adw.AlertDialog.new(question, "Este comando pede confirmação antes de rodar.")
        dialog.add_response("nao", "Não rodar")
        dialog.add_response("sim", "Rodar")
        dialog.set_response_appearance("sim", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("nao")
        dialog.set_close_response("nao")
        dialog.connect("response", lambda _d, resp: self.vm.confirm(resp == "sim"))
        dialog.present(self.win)

    def _tick(self) -> bool:
        if self.vm.state != RunVM.RUNNING:
            self._ticker = None
            return False
        self.status.set_label(self.vm.status_text())
        return True

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            GLib.source_remove(self._ticker)
            self._ticker = None

    def refresh(self) -> None:
        vm = self.vm
        running = vm.state == RunVM.RUNNING
        self.run_btn.set_visible(not running)
        self.run_btn.set_sensitive(vm.form.runnable and not vm.busy)
        self.run_btn.set_label("Rodar de novo" if vm.state in (RunVM.DONE, RunVM.FAILED) and vm.result else "Rodar")
        self.cancel_btn.set_visible(running)
        self.cancel_btn.set_sensitive(running and not vm.cancel_requested)
        self.form.set_sensitive(not vm.busy)
        self.status.set_label(vm.status_text())
        for cls in ("success", "error", "warning"):
            self.status.remove_css_class(cls)
        if vm.state == RunVM.DONE and vm.result:
            estado = vm.result.get("estado")
            self.status.add_css_class("success" if estado == "ok" else "warning" if estado == "cancelado" else "error")
        elif vm.state == RunVM.FAILED:
            self.status.add_css_class("error")
        self.job_label.set_label(f"job {vm.job}" if vm.job else "")
        if running and self._ticker is None:
            self._ticker = GLib.timeout_add(500, self._tick)


# ==========================================================================
# Histórico


class HistoryPage:
    def __init__(self, win: MainWindow, vm: HistoryVM):
        self.win = win
        self.vm = vm
        self.rows: list[Adw.ActionRow] = []
        clear = button("", "flat", icon="user-trash-symbolic", tooltip="Limpar o histórico")
        clear.connect("clicked", self._ask_clear)
        self.box = vbox()
        self.group = Adw.PreferencesGroup()
        self.group.set_title("Execuções")
        self.group.set_description("Guardado só nesta máquina. Clique pra ver a saída ou rodar de novo.")
        self.box.append(self.group)
        self.empty = Adw.StatusPage()
        self.empty.set_icon_name("document-open-recent-symbolic")
        self.empty.set_title("Nada ainda")
        self.empty.set_description("Os comandos que você rodar aparecem aqui.")
        self.stack = Gtk.Stack()
        self.stack.add_named(scrolled(self.box), "lista")
        self.stack.add_named(self.empty, "vazio")
        self.page = page("Histórico", self.stack, header_end=[clear])
        vm.changed.connect(self.rebuild)

    def rebuild(self) -> None:
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        if not self.vm.entries:
            self.stack.set_visible_child_name("vazio")
            return
        for e in self.vm.entries:
            icon = {"ok": "emblem-ok-symbolic", "rodando": "media-playback-start-symbolic"}.get(
                e.state, "dialog-warning-symbolic"
            )
            r = row(self.vm.title(e), self.vm.subtitle(e), activatable=True, icon=icon)
            r.connect("activated", lambda _r, entry=e: self.win.show_history_entry(entry))
            self.group.add(r)
            self.rows.append(r)
        self.stack.set_visible_child_name("lista")

    def _ask_clear(self, *_):
        dialog = Adw.AlertDialog.new("Limpar o histórico?", "Isso apaga as execuções guardadas nesta máquina.")
        dialog.add_response("nao", "Cancelar")
        dialog.add_response("sim", "Limpar")
        dialog.set_response_appearance("sim", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("nao")
        dialog.connect("response", lambda _d, resp: self.vm.clear() if resp == "sim" else None)
        dialog.present(self.win)


class HistoryEntryPage:
    def __init__(self, win: MainWindow, entry: Entry):
        self.win = win
        self.entry = entry
        box = vbox()
        box.append(label(HistoryVM.subtitle(entry), "heading"))
        if entry.job:
            box.append(label(f"job {entry.job}", "dim-label", "caption", selectable=True))
        if entry.error:
            box.append(label(entry.error, "error"))
        if entry.values:
            g = Adw.PreferencesGroup()
            g.set_title("Valores usados")
            for k, v in entry.values.items():
                shown = "\n".join(v) if isinstance(v, list) else str(v)
                g.add(row(k, shown))
            box.append(g)
        actions = Gtk.Box(spacing=12)
        actions.set_halign(Gtk.Align.CENTER)
        again = button("Rodar de novo", "pill", "suggested-action")
        again.connect("clicked", lambda *_: self.win.rerun(entry))
        copy = button("Copiar saída", "pill")
        copy.connect("clicked", self._copy)
        actions.append(again)
        actions.append(copy)
        box.append(actions)
        out = OutputView()
        out.append("stdout", entry.stdout)
        out.append("stderr", entry.stderr)
        if entry.truncated:
            out.append("stderr", "\n[a saída foi cortada em 256 KiB]\n")
        box.append(out.frame)
        self.page = page(f"{entry.command} em {entry.machine}", scrolled(box, max_width=900))

    def _copy(self, *_):
        copy_to_clipboard(self.page, self.entry.stdout + self.entry.stderr)
        self.win.toast("Saída copiada")


class CapturePage:
    """Ler a tela: tira um print, recorta um pedaço, extrai o texto e junta num bloco.

    Repete quantas vezes quiser; depois "Liberar" copia/salva o bloco e começa
    outro. O texto é extraído pela API do Claude (a chave fica nas Configurações).
    """

    def __init__(self, win: MainWindow, machine: str, prefill: bytes | None = None):
        self.win = win
        self.machine = machine
        self.vm = CaptureVM(win.service, win.make_extractor())
        self._syncing = False

        box = vbox()
        box.append(label(f"Tira um print de {machine}, recorta um pedaço e extrai o texto.", "dim-label"))

        box.append(label("O que fazer com o recorte", "heading"))
        mode_box = Gtk.Box(spacing=18)
        self.mode_ocr = Gtk.CheckButton(label="Ler o texto")
        self.mode_ask = Gtk.CheckButton(label="Perguntar ao Claude")
        self.mode_ask.set_group(self.mode_ocr)
        self.mode_ocr.set_active(True)
        self.mode_ocr.connect("toggled", lambda *_: self._mode_changed())
        self.mode_ask.connect("toggled", lambda *_: self._mode_changed())
        mode_box.append(self.mode_ocr)
        mode_box.append(self.mode_ask)
        box.append(mode_box)

        self.qgroup = Adw.PreferencesGroup()
        self.question = Adw.EntryRow()
        self.question.set_use_markup(False)
        self.question.set_title("Pergunta sobre o recorte (opcional)")
        self.question.set_tooltip_text(
            "Em branco, o Claude olha o recorte e faz o melhor: responde, explica ou completa."
        )
        self.qgroup.add(self.question)
        self.qgroup.set_visible(False)
        box.append(self.qgroup)

        top = Gtk.Box(spacing=12)
        top.set_halign(Gtk.Align.CENTER)
        self.shot_btn = button("Tirar print", "pill", "suggested-action")
        self.shot_btn.connect("clicked", lambda *_: self._take_shot())
        top.append(self.shot_btn)
        box.append(top)

        status_row = Gtk.Box(spacing=8)
        status_row.set_halign(Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        self.status = label("", "heading")
        status_row.append(self.spinner)
        status_row.append(self.status)
        box.append(status_row)

        img_head = Gtk.Box(spacing=6)
        img_head.append(label("Imagem", "heading"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        img_head.append(spacer)
        self.zoom_btn = Gtk.ToggleButton(label="Tamanho real")
        self.zoom_btn.add_css_class("flat")
        self.zoom_btn.set_tooltip_text("Ver em 100% pra mirar num texto pequeno (rola pra ver tudo)")
        self.zoom_btn.connect("toggled", lambda b: self.crop.set_full_size(b.get_active()))
        img_head.append(self.zoom_btn)
        self.img_head = img_head
        box.append(img_head)

        self.crop = CropView()
        box.append(self.crop.widget)

        # O botão de ler o recorte fica logo abaixo da imagem, onde a seleção é
        # feita, em vez de longe dela num passo numerado.
        under = Gtk.Box(spacing=12)
        under.set_halign(Gtk.Align.CENTER)
        self.extract_btn = button("Ler recorte", "pill", "suggested-action")
        self.extract_btn.connect("clicked", lambda *_: self._extract())
        self.extract_btn.set_sensitive(False)
        under.append(self.extract_btn)
        self.under = under
        box.append(under)

        # A área da imagem só aparece depois que tem um print, pra tela começar
        # limpa.
        for w in (self.img_head, self.crop.widget, self.under):
            w.set_visible(False)

        block_head = Gtk.Box(spacing=6)
        block_head.append(label("Texto juntado (dá pra editar)", "heading"))
        bspacer = Gtk.Box()
        bspacer.set_hexpand(True)
        block_head.append(bspacer)
        self.ask_block_btn = button("Perguntar ao Claude", "flat")
        self.ask_block_btn.set_tooltip_text("Manda o bloco inteiro (texto) pro Claude e junta a resposta aqui")
        self.ask_block_btn.connect("clicked", lambda *_: self._ask_block())
        block_head.append(self.ask_block_btn)
        box.append(block_head)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_editable(True)  # pode corrigir o OCR e a resposta à mão
        self.textview.set_cursor_visible(True)
        for side in ("top", "bottom", "left", "right"):
            getattr(self.textview, f"set_{side}_margin")(8)
        self.buffer = self.textview.get_buffer()
        self.buffer.connect("changed", self._text_edited)
        # O texto fica numa caixa de altura limitada que rola por dentro. Sem
        # isso ele crescia sem parar e empurrava os botões pra fora da janela.
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_min_content_height(200)
        text_scroll.set_max_content_height(320)
        text_scroll.set_propagate_natural_height(True)
        text_scroll.set_child(self.textview)
        frame = Gtk.Frame()
        frame.set_child(text_scroll)
        box.append(frame)

        actions = Gtk.Box(spacing=12)
        actions.set_halign(Gtk.Align.CENTER)
        self.copy_btn = button("Copiar bloco", "flat")
        self.copy_btn.connect("clicked", self._copy)
        self.release_btn = button("Liberar (salvar)", "pill", "suggested-action")
        self.release_btn.connect("clicked", lambda *_: self._release())
        self.type_btn = button(f"Digitar em {machine}", "flat")
        self.type_btn.set_tooltip_text("Cifra o bloco e manda pra máquina digitar (comando 'autodigitar' + 'tttttt')")
        self.type_btn.connect("clicked", lambda *_: self._autotype())
        clear_btn = button("Limpar", "flat")
        clear_btn.connect("clicked", lambda *_: self.vm.clear())
        actions.append(self.copy_btn)
        actions.append(self.release_btn)
        actions.append(self.type_btn)
        actions.append(clear_btn)
        box.append(actions)

        # Rolagem vertical por fora: a página inteira rola, então os botões lá
        # embaixo sempre dão pra alcançar, por mais texto que tenha no bloco.
        self.page = page(f"Ler tela · {machine}", scrolled(box, max_width=1200))
        self.vm.changed.connect(self._refresh)
        self.vm.block_changed.connect(self._block_changed)
        if prefill is not None:
            try:
                self.crop.set_image(prefill)
                self._show_image_area()
                self.extract_btn.set_sensitive(True)
                self.status.set_label("Arraste o mouse sobre o texto e clique em “Ler recorte”.")
            except Exception:
                self.status.set_label("Clique em “Tirar print” pra começar.")
        else:
            self.status.set_label("Clique em “Tirar print” pra começar.")
        self._refresh()

    def _show_image_area(self) -> None:
        for w in (self.img_head, self.crop.widget, self.under):
            w.set_visible(True)

    def _mode(self) -> str:
        return "pergunta" if self.mode_ask.get_active() else "ocr"

    def _mode_changed(self) -> None:
        pergunta = self._mode() == "pergunta"
        self.qgroup.set_visible(pergunta)
        self.extract_btn.set_label("Perguntar" if pergunta else "Ler recorte")

    def _take_shot(self) -> None:
        self.status.set_label("tirando o print…")
        self.shot_btn.set_sensitive(False)
        self._busy(True)

        def done(outcome) -> None:
            self.shot_btn.set_sensitive(True)
            self._busy(False)
            if outcome.artifact is not None:
                try:
                    self.crop.set_image(outcome.artifact)
                except Exception as e:
                    self.status.set_label(f"não consegui mostrar a imagem ({e})")
                    return
                self._show_image_area()
                self.zoom_btn.set_active(False)
                self.extract_btn.set_sensitive(True)
                self.status.set_label(
                    "Pronto. Arraste o mouse sobre o texto e clique em “Ler recorte”, logo abaixo da imagem."
                )
            else:
                r = outcome.result or {}
                detail = outcome.text_stderr.strip() or r.get("erro") or "o comando não devolveu imagem"
                self.status.set_label(f"o print falhou: {detail[:200]}")

        def fail(msg: str) -> None:
            self.shot_btn.set_sensitive(True)
            self._busy(False)
            self.status.set_label(msg)

        self.win.service.run(self.machine, "screenshot", {"cursor": False}, None, lambda ev: None, done, fail)

    def _extract(self) -> None:
        if not self.crop.has_image:
            return
        try:
            png = self.crop.crop_png()
        except Exception as e:
            self.status.set_label(f"não consegui recortar ({e})")
            return
        pergunta = self._mode() == "pergunta"
        q = self.question.get_text() if pergunta else None
        self.status.set_label("perguntando ao Claude…" if pergunta else "lendo o recorte com o Claude…")
        self.vm.add_crop(png, self._mode(), q)

    def _block_changed(self, text: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.buffer.set_text(text)
        self._syncing = False

    def _text_edited(self, *_):
        if self._syncing:
            return
        self._syncing = True
        self.vm.set_block(self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False))
        self._syncing = False

    def _busy(self, on: bool) -> None:
        """Mostra o rodinha enquanto o Claude trabalha, pra não parecer travado."""
        self.spinner.set_visible(on)
        if on:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _refresh(self) -> None:
        busy = self.vm.busy
        self._busy(busy)
        self.extract_btn.set_sensitive(self.crop.has_image and not busy)
        self.copy_btn.set_sensitive(self.vm.has_text)
        self.release_btn.set_sensitive(self.vm.has_text)
        self.type_btn.set_sensitive(self.vm.has_text)
        self.ask_block_btn.set_sensitive(self.vm.has_text and not busy)
        if self.vm.error:
            self.status.set_label(self.vm.error)
        elif not busy and self.vm.snippets:
            self.status.set_label(f"{self.vm.snippets} recorte(s) no bloco. Tire outro print ou libere o bloco.")

    def _copy(self, *_):
        copy_to_clipboard(self.page, self.vm.block)
        self.win.toast("Bloco copiado")

    def _release(self) -> None:
        text = self.vm.release()
        copy_to_clipboard(self.page, text)
        self.win.save_text(text, suggested_name=f"{self.machine}-texto")
        self.win.toast("Bloco copiado e começando um novo")

    def _autotype(self) -> None:
        """Cifra o bloco e manda pra máquina autodigitar (comando 'autodigitar')."""
        from ..core import autotype

        bloco = self.vm.block
        if not bloco.strip():
            self.win.toast("o bloco está vazio")
            return
        try:
            cfg = autotype.load_settings()
            cifrado = autotype.encrypt(bloco, cfg.senha)
        except Exception as e:
            self.win.toast(str(e))
            return
        if len(cifrado) > MAX_MULTILINE:
            self.win.toast(f"o bloco é grande demais (cifrado passa de {MAX_MULTILINE})")
            return
        eta = autotype.estimate_seconds(len(bloco), atraso=3, cps=cfg.cps)
        aviso = " (pode estourar o tempo, mande um bloco menor)" if eta > autotype.COMMAND_TIMEOUT - 30 else ""
        self.win.toast(f"mandando pra {self.machine} digitar, uns {autotype.format_eta(eta)}{aviso}")

        def done(outcome) -> None:
            r = outcome.result or {}
            if r.get("codigo") == 0:
                self.win.toast(outcome.text_stdout.strip()[:140] or f"{self.machine} digitou o bloco")
            else:
                msg = outcome.text_stderr.strip() or r.get("erro") or f"código {r.get('codigo')}"
                self.win.toast(f"não digitou: {msg[:140]}")

        self.win.service.run(
            self.machine, "autodigitar", {"texto": cifrado}, None, lambda ev: None, done, self.win.toast
        )

    def _ask_block(self) -> None:
        """Manda o bloco inteiro (texto) pro Claude e junta a resposta no bloco."""
        from ..core import vision

        bloco = self.vm.block
        if not bloco.strip():
            self.win.toast("o bloco está vazio")
            return
        self.status.set_label("perguntando ao Claude sobre o bloco…")
        self._busy(True)

        def work():
            return vision.ask_text(bloco, settings=vision.load_settings())

        def done(resposta: str) -> None:
            self._busy(False)
            self.vm.append_text(resposta)
            self.status.set_label("resposta do Claude juntada no bloco")

        def fail(msg: str) -> None:
            self._busy(False)
            self.status.set_label(msg)

        self.win.service.submit(work, done, fail)
