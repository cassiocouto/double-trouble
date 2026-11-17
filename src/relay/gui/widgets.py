"""Peças pequenas de GTK usadas pelas telas.

Regra: todo texto que vem do agente (nomes, descrições, erros) entra como
texto puro. As linhas do libadwaita interpretam markup por padrão, então
desligamos isso (`use_markup=False`) ou escapamos o texto.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from .form import Field, FormModel  # noqa: E402

DEFAULT_ICON = "system-run-symbolic"


def esc(text: object) -> str:
    return GLib.markup_escape_text(str(text if text is not None else ""))


def row(title: str, subtitle: str = "", activatable: bool = False, icon: str | None = None) -> Adw.ActionRow:
    r = Adw.ActionRow()
    r.set_use_markup(False)
    r.set_title(title)
    if subtitle:
        r.set_subtitle(subtitle)
    r.set_subtitle_lines(3)
    if icon:
        r.add_prefix(Gtk.Image.new_from_icon_name(icon))
    if activatable:
        r.set_activatable(True)
        r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    return r


def label(text: str, *classes: str, wrap: bool = True, selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, wrap=wrap, xalign=0, selectable=selectable)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def button(text: str, *classes: str, icon: str | None = None, tooltip: str = "") -> Gtk.Button:
    b = Gtk.Button(icon_name=icon) if icon and not text else Gtk.Button(label=text)
    for c in classes:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    return b


def toast(text: str, timeout: int = 4) -> Adw.Toast:
    t = Adw.Toast.new(esc(text))
    t.set_timeout(timeout)
    return t


def copy_to_clipboard(widget: Gtk.Widget, text: str) -> None:
    value = GObject.Value(GObject.TYPE_STRING, text)
    widget.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(value))


def scrolled(child: Gtk.Widget, max_width: int = 720) -> Gtk.ScrolledWindow:
    clamp = Adw.Clamp()
    clamp.set_maximum_size(max_width)
    clamp.set_child(child)
    sw = Gtk.ScrolledWindow()
    sw.set_vexpand(True)
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.set_child(clamp)
    return sw


def vbox(spacing: int = 18, margin: int = 18) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(margin)
    return box


def page(title: str, content: Gtk.Widget, header_end: list[Gtk.Widget] | None = None) -> Adw.NavigationPage:
    header = Adw.HeaderBar()
    for w in header_end or []:
        header.pack_end(w)
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(content)
    p = Adw.NavigationPage.new(view, title)
    p.header = header  # type: ignore[attr-defined]
    p.toolbar = view  # type: ignore[attr-defined]
    return p


class OutputView(Gtk.ScrolledWindow):
    """Saída ao vivo. stderr aparece em vermelho. Rola sozinho pro fim."""

    def __init__(self):
        super().__init__()
        self.set_min_content_height(260)
        self.set_vexpand(True)
        self.text = Gtk.TextView()
        self.text.set_editable(False)
        self.text.set_cursor_visible(False)
        self.text.set_monospace(True)
        self.text.set_wrap_mode(Gtk.WrapMode.CHAR)
        for side in ("top", "bottom", "left", "right"):
            getattr(self.text, f"set_{side}_margin")(12)
        self.buffer = self.text.get_buffer()
        self.buffer.create_tag("stderr", foreground="#e01b24")
        self.end_mark = self.buffer.create_mark("fim", self.buffer.get_end_iter(), False)
        self.set_child(self.text)
        frame = Gtk.Frame()
        frame.set_child(self)
        self.frame = frame

    def clear(self) -> None:
        self.buffer.set_text("")

    def append(self, stream: str, text: str) -> None:
        end = self.buffer.get_end_iter()
        if stream == "stderr":
            self.buffer.insert_with_tags_by_name(end, text, "stderr")
        else:
            self.buffer.insert(end, text)
        self.text.scroll_mark_onscreen(self.end_mark)


class FormView:
    """Liga os campos do FormModel a widgets do libadwaita."""

    def __init__(self, form: FormModel, values: dict[str, Any]):
        self.form = form
        self.group = Adw.PreferencesGroup()
        self.group.set_title("Parâmetros" if form.uses_params else "Argumentos")
        self.widgets: dict[str, Gtk.Widget] = {}
        self.error_labels: dict[str, Gtk.Label] = {}
        for f in form.fields:
            widget = self._make(f, values.get(f.name))
            self.widgets[f.name] = widget
            self.group.add(widget)
            err = label("", "error", "caption")
            err.set_visible(False)
            err.set_margin_start(12)
            self.error_labels[f.name] = err
            self.group.add(err)

    @property
    def empty(self) -> bool:
        return not self.form.fields

    def _make(self, f: Field, value: Any) -> Gtk.Widget:
        title = f.label + (" *" if f.required else "")
        if f.kind in ("texto", "inteiro_opcional", "segredo"):
            w = Adw.PasswordEntryRow() if f.kind == "segredo" else Adw.EntryRow()
            w.set_use_markup(False)
            w.set_title(title)
            w.set_text("" if value is None else str(value))
            if f.help:
                w.set_tooltip_text(f.help)
            return w
        if f.kind == "inteiro":
            lo = f.minimum if f.minimum is not None else -(2**31)
            hi = f.maximum if f.maximum is not None else 2**31 - 1
            w = Adw.SpinRow.new_with_range(lo, hi, 1)
            w.set_use_markup(False)
            w.set_title(title)
            w.set_digits(0)
            w.set_value(float(value if isinstance(value, int) else lo))
            if f.help:
                w.set_subtitle(f.help)
            return w
        if f.kind == "booleano":
            w = Adw.SwitchRow()
            w.set_use_markup(False)
            w.set_title(title)
            w.set_active(bool(value))
            if f.help:
                w.set_subtitle(f.help)
            return w
        if f.kind == "escolha":
            w = Adw.ComboRow()
            w.set_use_markup(False)
            w.set_title(title)
            w.set_model(Gtk.StringList.new(f.options))
            if value in f.options:
                w.set_selected(f.options.index(value))
            if f.help:
                w.set_subtitle(f.help)
            return w
        # texto_longo e argumentos: caixa de várias linhas
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.append(label(title, "heading"))
        if f.help:
            box.append(label(f.help, "dim-label", "caption"))
        tv = Gtk.TextView()
        tv.set_monospace(f.kind == "argumentos")
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_size_request(-1, 90)
        for side in ("top", "bottom", "left", "right"):
            getattr(tv, f"set_{side}_margin")(8)
        tv.get_buffer().set_text("" if value is None else str(value))
        frame = Gtk.Frame()
        frame.set_child(tv)
        box.append(frame)
        box.textview = tv  # type: ignore[attr-defined]
        return box

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.form.fields:
            w = self.widgets[f.name]
            if f.kind in ("texto", "inteiro_opcional", "segredo"):
                out[f.name] = w.get_text()
            elif f.kind == "inteiro":
                out[f.name] = round(w.get_value())
            elif f.kind == "booleano":
                out[f.name] = w.get_active()
            elif f.kind == "escolha":
                i = w.get_selected()
                out[f.name] = f.options[i] if 0 <= i < len(f.options) else None
            else:
                buf = w.textview.get_buffer()
                out[f.name] = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return out

    def show_errors(self, errors: dict[str, str]) -> None:
        for name, lbl in self.error_labels.items():
            msg = errors.get(name, "")
            lbl.set_label(msg)
            lbl.set_visible(bool(msg))
            w = self.widgets[name]
            if msg:
                w.add_css_class("error")
            else:
                w.remove_css_class("error")

    def set_sensitive(self, sensitive: bool) -> None:
        for w in self.widgets.values():
            w.set_sensitive(sensitive)
