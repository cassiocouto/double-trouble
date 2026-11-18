"""relay-gui: o aplicativo (app id dev.cass.Relay)."""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
import tempfile

_CRASH_FILE = None  # mantém o arquivo do faulthandler vivo enquanto o app roda


def _missing_gtk_message(exc: BaseException) -> str:
    return (
        "A interface precisa do GTK 4 e da libadwaita 1.5 pelo PyGObject.\n"
        "Instale com:\n\n"
        "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n\n"
        f"(detalhe: {exc})"
    )


def main(argv: list[str] | None = None) -> int:
    # Se o programa cair com segfault, grava num arquivo a pilha Python até a
    # chamada que derrubou tudo (funciona até pra crash em C, dentro do GTK).
    # Arquivo (não terminal) pra não embaralhar quando mais de uma thread cai.
    with contextlib.suppress(Exception):
        global _CRASH_FILE
        _CRASH_FILE = open(os.path.join(tempfile.gettempdir(), "relay-crash.txt"), "w")  # noqa: SIM115
        faulthandler.enable(file=_CRASH_FILE, all_threads=True)
    # Desenho por software, pra não depender da placa. O renderizador GL do GTK
    # trava em várias placas, e o backend de mídia (GStreamer) puxa GL por fora.
    # (O nome dos flags de GDK_DEBUG muda entre versões, então não mexemos lá.)
    # Quem tiver uma placa boa reativa pelas variáveis.
    os.environ.setdefault("GSK_RENDERER", "cairo")
    os.environ.setdefault("GTK_MEDIA", "none")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
    except (ImportError, ValueError) as e:
        print(_missing_gtk_message(e), file=sys.stderr)
        return 1
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        print(_missing_gtk_message(f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}"), file=sys.stderr)
        return 1

    from .. import APP_ID
    from .window import MainWindow, build_about

    args = list(sys.argv if argv is None else argv)
    checking = "--checar" in args
    if checking:
        args.remove("--checar")

    class RelayApp(Adw.Application):
        def __init__(self):
            if checking:
                # App id separado e sem instância única: não conversa com uma janela já aberta.
                super().__init__(application_id=APP_ID + ".Checar", flags=Gio.ApplicationFlags.NON_UNIQUE)
            else:
                super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window: MainWindow | None = None
            self.exit_code = 0

        def do_startup(self):
            Adw.Application.do_startup(self)
            for name, handler in (
                ("historico", lambda *_: self.window and self.window.show_history()),
                ("configuracoes", lambda *_: self.window and self.window.show_preferences()),
                ("sobre", lambda *_: self.window and build_about(self.window).present(self.window)),
                ("mostrar", lambda *_: self.window and self.window.present()),
                ("sair", lambda *_: self.quit()),
            ):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", handler)
                self.add_action(action)
            self.set_accels_for_action("app.sair", ["<Control>q"])
            self.set_accels_for_action("app.configuracoes", ["<Control>comma"])
            self.set_accels_for_action("app.historico", ["<Control>h"])

        def do_activate(self):
            if checking:
                self._self_check()
                return
            if self.window is None:
                self.window = MainWindow(self)
            self.window.present()

        def _self_check(self):
            import traceback

            from . import selfcheck

            self.hold()
            try:
                self.window = MainWindow(self)
            except Exception:
                selfcheck.report([("janela principal", traceback.format_exc())])
                self.exit_code = 1
            else:
                self.exit_code = selfcheck.report(selfcheck.run_checks(self.window))
                self.window.close()
            finally:
                self.release()
                self.quit()

    app = RelayApp()
    code = app.run(args)
    return code or app.exit_code


if __name__ == "__main__":
    sys.exit(main())
