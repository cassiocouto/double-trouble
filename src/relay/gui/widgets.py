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

from . import geometry  # noqa: E402
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


def detox_button(textview: Gtk.TextView, win: Any, *, on_done: Any = None) -> Gtk.Button:
    """Botão 'Detoxify' pra qualquer caixa de texto editável.

    Reescreve o texto da caixa pra soar humano (tira travessão, clichê, hype...),
    com a MESMA chave da API do Claude do resto da interface, e troca o texto
    da caixa pela versão limpa. A chamada roda num thread (win.service.submit),
    então a tela não trava. `on_done(texto)` é chamado depois, se vier.
    """
    b = button("Detoxify", "flat")
    b.set_tooltip_text("Reescreve o texto pra soar humano, tirando os vícios de IA (usa a chave do Claude)")

    def current_text() -> str:
        buf = textview.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def run(*_a: object) -> None:
        from ..core import detox, vision

        texto = current_text()
        if not texto.strip():
            win.toast("não tem texto pra limpar")
            return
        b.set_sensitive(False)
        b.set_label("limpando…")

        def work() -> str:
            return detox.detoxify_text(texto, settings=vision.load_settings())

        def done(limpo: str) -> None:
            b.set_sensitive(True)
            b.set_label("Detoxify")
            textview.get_buffer().set_text(limpo)
            win.toast("texto reescrito com o Detoxify")
            if on_done is not None:
                on_done(limpo)

        def fail(msg: str) -> None:
            b.set_sensitive(True)
            b.set_label("Detoxify")
            win.toast(msg)

        win.service.submit(work, done, fail)

    b.connect("clicked", run)
    return b


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
        # Cresce com a saída até um teto e só então rola, pra uma saída de uma
        # linha não virar uma caixa preta gigante.
        self.set_min_content_height(90)
        self.set_max_content_height(360)
        self.set_propagate_natural_height(True)
        self.set_vexpand(False)
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

    def __init__(self, form: FormModel, values: dict[str, Any], win: Any = None):
        self.form = form
        self.win = win  # se vier, as caixas de texto longo ganham o botão Detoxify
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
        tv = Gtk.TextView()
        tv.set_monospace(f.kind == "argumentos")
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_size_request(-1, 90)
        for side in ("top", "bottom", "left", "right"):
            getattr(tv, f"set_{side}_margin")(8)
        tv.get_buffer().set_text("" if value is None else str(value))
        head = Gtk.Box(spacing=6)
        head.append(label(title, "heading"))
        # Só texto corrido (texto_longo) ganha Detoxify; "argumentos" é linha de
        # comando, não prosa, então fica de fora.
        if self.win is not None and f.kind == "texto_longo":
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            head.append(spacer)
            head.append(detox_button(tv, self.win))
        box.append(head)
        if f.help:
            box.append(label(f.help, "dim-label", "caption"))
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
        self._on_read = None
        self.read_btn = button("Ler o texto (OCR)", "flat")
        self.read_btn.set_tooltip_text("Recortar um pedaço e extrair o texto com o Claude")
        self.read_btn.connect("clicked", lambda *_: self._on_read and self._on_read(self.data))
        self.read_btn.set_visible(False)
        head.append(self.read_btn)
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

    def show(self, data: bytes, is_image: bool, suggested_name: str = "arquivo", on_read=None) -> None:
        self.data = data
        self.name = suggested_name
        self._on_read = on_read
        shown_image = False
        if is_image:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                self.picture.set_paintable(texture)
                self.width, self.height = texture.get_width(), texture.get_height()
                self.title.set_label("Imagem")
                self.frame.set_visible(True)
                self.zoom_btn.set_visible(True)
                self.read_btn.set_visible(on_read is not None)
                self._apply_zoom()
                shown_image = True
            except GLib.Error:
                shown_image = False
        if not shown_image:
            self.frame.set_visible(False)
            self.zoom_btn.set_visible(False)
            self.read_btn.set_visible(False)
            self.title.set_label("Arquivo")
            self.note.set_label(f"{len(data) // 1024} KiB · use Salvar pra guardar")

    def _toggle_zoom(self, btn) -> None:
        self.full_size = btn.get_active()
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self.full_size:
            # 100%: a imagem pede o tamanho real e a área rola nos dois sentidos.
            self.picture.set_can_shrink(False)
            self.picture.set_halign(Gtk.Align.START)
            self.picture.set_valign(Gtk.Align.START)
            self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        else:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
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


_CROP_CSS = """
.crop-selection {
  border: 2px solid #3584e4;
  background-color: rgba(53, 132, 228, 0.18);
  border-radius: 2px;
}
.crop-size {
  background-color: rgba(0, 0, 0, 0.78);
  color: #ffffff;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.crop-hint {
  background-color: rgba(0, 0, 0, 0.72);
  color: #ffffff;
  padding: 6px 12px;
  border-radius: 6px;
}
"""

_crop_css_loaded = False


def _ensure_crop_css() -> None:
    """Carrega o CSS do recorte uma vez só, se houver uma tela."""
    global _crop_css_loaded
    if _crop_css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    if hasattr(provider, "load_from_string"):
        provider.load_from_string(_CROP_CSS)
    else:
        provider.load_from_data(_CROP_CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _crop_css_loaded = True


class CropView:
    """Mostra uma imagem e deixa arrastar um retângulo pra recortar um pedaço.

    A imagem é um `Gtk.Picture` (o mesmo jeito que já funciona na tela do print).
    O retângulo de seleção, o rótulo e a dica são widgets de verdade por cima da
    imagem, posicionados por margem num `Gtk.Overlay` — nada de `Gtk.Fixed` (que
    entrava em laço de alocação de tamanho e estourava a pilha) e nada de cairo
    (que nem sempre está disponível). As camadas de cima não pegam clique
    (`can_target = False`), então o arrasto vai direto pro `Picture`. Com
    "tamanho real" a imagem fica em 100% e a área rola. crop_png() devolve o
    recorte, ou a imagem inteira se nada foi selecionado.
    """

    MIN_PIXELS = 4
    HINT = "Arraste o mouse sobre o texto que você quer ler"

    def __init__(self):
        _ensure_crop_css()
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.iw = self.ih = 0
        self.full_size = False
        self._start: tuple[float, float] | None = None
        self._cur: tuple[float, float] | None = None

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_cursor(Gdk.Cursor.new_from_name("crosshair", None))

        # O retângulo e o rótulo ficam no canto de cima à esquerda e a margem
        # empurra pro lugar. Sem Gtk.Fixed, que dava laço de alocação de tamanho.
        self.selbox = Gtk.Box()
        self.selbox.add_css_class("crop-selection")
        self.selbox.set_halign(Gtk.Align.START)
        self.selbox.set_valign(Gtk.Align.START)
        self.selbox.set_can_target(False)
        self.selbox.set_visible(False)
        self.sizelbl = Gtk.Label()
        self.sizelbl.add_css_class("crop-size")
        self.sizelbl.set_halign(Gtk.Align.START)
        self.sizelbl.set_valign(Gtk.Align.START)
        self.sizelbl.set_can_target(False)
        self.sizelbl.set_visible(False)

        self.hint = label(self.HINT, "crop-hint")
        self.hint.set_halign(Gtk.Align.CENTER)
        self.hint.set_valign(Gtk.Align.START)
        self.hint.set_margin_top(14)
        self.hint.set_can_target(False)
        self.hint.set_visible(False)

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.picture)
        self.overlay.add_overlay(self.selbox)
        self.overlay.add_overlay(self.sizelbl)
        self.overlay.add_overlay(self.hint)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.picture.add_controller(drag)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_min_content_height(440)
        self.scroller.set_child(self.overlay)
        frame = Gtk.Frame()
        frame.set_child(self.scroller)
        self.widget = frame

    def set_image(self, data: bytes) -> None:
        self.pixbuf = pixbuf_from_png(data)  # guardado só pra recortar
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        # A textura que aparece na tela vem dos bytes do PNG (igual ao ImageView,
        # que funciona), e NÃO de new_for_pixbuf: um pixbuf com rowstride "folgado"
        # vira uma textura cujo download, na hora de desenhar, lê fora da memória
        # e dá segfault (gdk_texture_downloader_download_into).
        self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
        self._start = self._cur = None
        self._apply_zoom()
        self._place()

    @property
    def has_image(self) -> bool:
        return self.pixbuf is not None

    def set_full_size(self, flag: bool) -> None:
        self.full_size = flag
        self._start = self._cur = None  # a coordenada muda de escala
        self._apply_zoom()
        self._place()

    def _apply_zoom(self) -> None:
        if self.full_size:
            self.picture.set_can_shrink(False)
            self.picture.set_halign(Gtk.Align.START)
            self.picture.set_valign(Gtk.Align.START)
            self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        else:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    def _geom(self):
        w, h = self.picture.get_width(), self.picture.get_height()
        if not (w and h and self.iw and self.ih):
            return None
        if self.full_size:
            return 1.0, 0.0, 0.0, float(self.iw), float(self.ih)
        scale = min(w / self.iw, h / self.ih)
        dw, dh = self.iw * scale, self.ih * scale
        return scale, (w - dw) / 2, (h - dh) / 2, dw, dh

    def _begin(self, gesture, x, y):
        self._start = (x, y)
        self._cur = (x, y)
        self._place()

    def _update(self, gesture, ox, oy):
        if self._start:
            self._cur = (self._start[0] + ox, self._start[1] + oy)
            self._place()

    def _end(self, gesture, ox, oy):
        self._update(gesture, ox, oy)

    def _place(self) -> None:
        """Põe o retângulo e o rótulo no lugar, ou mostra a dica se não há seleção."""
        if not (self._start and self._cur):
            self.selbox.set_visible(False)
            self.sizelbl.set_visible(False)
            self.hint.set_visible(self.pixbuf is not None)
            return
        self.hint.set_visible(False)
        x0, y0 = self._start
        x1, y1 = self._cur
        rx, ry = max(0, int(min(x0, x1))), max(0, int(min(y0, y1)))
        rw, rh = int(abs(x1 - x0)), int(abs(y1 - y0))
        self.selbox.set_size_request(max(1, rw), max(1, rh))
        self.selbox.set_margin_start(rx)
        self.selbox.set_margin_top(ry)
        self.selbox.set_visible(True)
        sel = self._pixels(enforce_min=False)
        if sel is not None:
            self.sizelbl.set_label(f"{sel[2]} × {sel[3]} px")
            ly = ry - 24 if ry >= 24 else ry + 4
            self.sizelbl.set_margin_start(rx)
            self.sizelbl.set_margin_top(max(0, ly))
            self.sizelbl.set_visible(True)
        else:
            self.sizelbl.set_visible(False)

    def _pixels(self, enforce_min: bool = True):
        geom = self._geom()
        if not (self._start and self._cur and geom):
            return None
        scale, ox, oy, _dw, _dh = geom
        rect = geometry.crop_rect(self._start, self._cur, scale, ox, oy, self.iw, self.ih)
        if rect is None:
            return None
        if enforce_min and (rect[2] < self.MIN_PIXELS or rect[3] < self.MIN_PIXELS):
            return None
        return rect

    def selection_pixels(self):
        return self._pixels(enforce_min=True)

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
        self._place()
