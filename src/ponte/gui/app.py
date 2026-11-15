"""ponte-gui: o aplicativo (app id dev.cass.Ponte)."""

from __future__ import annotations

import sys


def _missing_gtk_message(exc: BaseException) -> str:
    return (
        "A interface precisa do GTK 4 e da libadwaita 1.5 pelo PyGObject.\n"
        "Instale com:\n\n"
        "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n\n"
        f"(detalhe: {exc})"
    )


def main(argv: list[str] | None = None) -> int:
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

    class PonteApp(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window: MainWindow | None = None

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
            if self.window is None:
                self.window = MainWindow(self)
            self.window.present()

    app = PonteApp()
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
