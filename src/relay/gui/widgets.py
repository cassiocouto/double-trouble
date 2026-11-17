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
import gi as _gi  # noqa: E402

_gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

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


class ImageView:
    """Mostra a imagem que um comando devolveu, com botão de salvar.

    Pra arquivo que não é imagem (um PDF, por exemplo), mostra só o tamanho e
    o botão de salvar.
    """

    def __init__(self, win):
        self.win = win
        self.data = b""
        self.name = "arquivo"
        self.full_size = False  # False = cabe na largura, True = tamanho real (rola)
        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.container.set_margin_top(6)
        head = Gtk.Box(spacing=6)
        self.title = label("Imagem", "heading")
        head.append(self.title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        self.zoom_btn = Gtk.ToggleButton(label="Tamanho real")
        self.zoom_btn.add_css_class("flat")
        self.zoom_btn.set_tooltip_text("Ver em 100% (rola pra ver tudo) ou caber na largura")
        self.zoom_btn.connect("toggled", self._toggle_zoom)
        head.append(self.zoom_btn)
        save = button("Salvar…", "flat")
        save.connect("clicked", self._save)
        head.append(save)
        self.container.append(head)

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        # Área rolável e alta, pra um print caber de verdade. Em 100% os dois
        # scrollbars deixam você andar pela imagem inteira.
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_min_content_height(480)
        self.scroller.set_child(self.picture)
        frame = Gtk.Frame()
        frame.set_child(self.scroller)
        self.frame = frame
        self.container.append(frame)
        self.note = label("", "dim-label", "caption")
        self.container.append(self.note)

    def show(self, data: bytes, is_image: bool, suggested_name: str = "arquivo") -> None:
        self.data = data
        self.name = suggested_name
        shown_image = False
        if is_image:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                self.picture.set_paintable(texture)
                self.width, self.height = texture.get_width(), texture.get_height()
                self.title.set_label("Imagem")
                self.frame.set_visible(True)
                self.zoom_btn.set_visible(True)
                self._apply_zoom()
                shown_image = True
            except GLib.Error:
                shown_image = False
        if not shown_image:
            self.frame.set_visible(False)
            self.zoom_btn.set_visible(False)
            self.title.set_label("Arquivo")
            self.note.set_label(f"{len(data) // 1024} KiB · use Salvar pra guardar")

    def _toggle_zoom(self, btn) -> None:
        self.full_size = btn.get_active()
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self.full_size:
            # 100%: a imagem pede o tamanho real e a área rola.
            self.picture.set_can_shrink(False)
            self.picture.set_halign(Gtk.Align.START)
            self.picture.set_valign(Gtk.Align.START)
            self.scroller.set_hscrollbar_policy(Gtk.PolicyType.AUTOMATIC)
        else:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.scroller.set_hscrollbar_policy(Gtk.PolicyType.NEVER)
        self.note.set_label(f"{self.width}x{self.height} · {len(self.data) // 1024} KiB")

    def _save(self, *_):
        dialog = Gtk.FileDialog()
        stamp = GLib.DateTime.new_now_local().format("%Y%m%d-%H%M%S")
        dialog.set_initial_name(f"{self.name}-{stamp}{self._extension()}")

        def done(dlg, result):
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return  # o usuário cancelou
            try:
                Gio.File.replace_contents(gfile, self.data, None, False, Gio.FileCreateFlags.NONE, None)
                self.win.toast("Arquivo salvo")
            except GLib.Error as e:
                self.win.toast(f"Não consegui salvar ({e.message})")

        dialog.save(self.win, None, done)

    def _extension(self) -> str:
        # Descobre a extensão pelos primeiros bytes (igual ao agente).
        from ..core.media import sniff

        return sniff(self.data).extensao


def pixbuf_from_png(data: bytes) -> GdkPixbuf.Pixbuf:
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    pb = loader.get_pixbuf()
    if pb is None:
        raise ValueError("imagem inválida")
    return pb


def png_from_pixbuf(pb: GdkPixbuf.Pixbuf) -> bytes:
    ok, buf = pb.save_to_bufferv("png", [], [])
    if not ok:
        raise ValueError("não consegui gerar o PNG")
    return bytes(buf)


class CropView:
    """Mostra uma imagem e deixa arrastar um retângulo pra recortar um pedaço.

    O retângulo é desenhado numa camada por cima da imagem. As coordenadas da
    tela viram pixels da imagem levando em conta a escala e as bordas (a imagem
    cabe na área, centralizada). crop_png() devolve o recorte, ou a imagem
    inteira se nada foi selecionado.
    """

    MIN_PIXELS = 4

    def __init__(self):
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.iw = self.ih = 0
        self._start: tuple[float, float] | None = None
        self._cur: tuple[float, float] | None = None
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.picture)
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        self.overlay.add_overlay(self.area)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.area.add_controller(drag)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_min_content_height(420)
        self.scroller.set_child(self.overlay)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.widget = self.scroller

    def set_image(self, data: bytes) -> None:
        self.pixbuf = pixbuf_from_png(data)
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(self.pixbuf))
        self._start = self._cur = None
        self.area.queue_draw()

    @property
    def has_image(self) -> bool:
        return self.pixbuf is not None

    def _geom(self):
        w, h = self.area.get_width(), self.area.get_height()
        if not (w and h and self.iw and self.ih):
            return None
        scale = min(w / self.iw, h / self.ih)
        dw, dh = self.iw * scale, self.ih * scale
        return scale, (w - dw) / 2, (h - dh) / 2, dw, dh

    def _begin(self, gesture, x, y):
        self._start = (x, y)
        self._cur = (x, y)
        self.area.queue_draw()

    def _update(self, gesture, ox, oy):
        if self._start:
            self._cur = (self._start[0] + ox, self._start[1] + oy)
            self.area.queue_draw()

    def _end(self, gesture, ox, oy):
        self._update(gesture, ox, oy)

    def _draw(self, area, cr, w, h):
        if not (self._start and self._cur):
            return
        x0, y0 = self._start
        x1, y1 = self._cur
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        cr.set_source_rgba(0.2, 0.5, 0.9, 0.2)
        cr.rectangle(rx, ry, rw, rh)
        cr.fill()
        cr.set_source_rgba(0.2, 0.5, 0.9, 0.9)
        cr.set_line_width(1.5)
        cr.rectangle(rx, ry, rw, rh)
        cr.stroke()

    def selection_pixels(self):
        geom = self._geom()
        if not (self._start and self._cur and geom):
            return None
        scale, ox, oy, _dw, _dh = geom
        xs = sorted((self._start[0], self._cur[0]))
        ys = sorted((self._start[1], self._cur[1]))
        x0 = max(0, (xs[0] - ox) / scale)
        y0 = max(0, (ys[0] - oy) / scale)
        x1 = min(self.iw, (xs[1] - ox) / scale)
        y1 = min(self.ih, (ys[1] - oy) / scale)
        px, py = round(x0), round(y0)
        pw, ph = round(x1 - x0), round(y1 - y0)
        if pw < self.MIN_PIXELS or ph < self.MIN_PIXELS:
            return None
        return px, py, pw, ph

    def crop_png(self) -> bytes:
        if self.pixbuf is None:
            raise ValueError("não tem imagem pra recortar")
        sel = self.selection_pixels()
        if sel is None:
            return png_from_pixbuf(self.pixbuf)
        px, py, pw, ph = sel
        return png_from_pixbuf(self.pixbuf.new_subpixbuf(px, py, pw, ph))

    def clear_selection(self) -> None:
        self._start = self._cur = None
        self.area.queue_draw()
