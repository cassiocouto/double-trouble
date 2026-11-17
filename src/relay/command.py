"""Ajuda pequena pra quem escreve comandos do Relay.

    from relay.command import caller, detach, params

    p = params()               # {"texto": "oi", ...}, já validado pelo agente
    print(f"oi, {caller()}")   # nome da máquina que pediu
    detach(["xdg-open", url])  # abre um programa que continua depois do comando

Os parâmetros chegam como um objeto JSON no stdin. Eles não aparecem no `ps`
de outros usuários (ao contrário de argumentos de linha de comando).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from typing import Any

_cache: dict[str, Any] | None = None

SESSION_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")


def params() -> dict[str, Any]:
    """Os parâmetros que o agente validou. Vazio se o comando não tem PARAMETROS."""
    global _cache
    if _cache is None:
        if sys.stdin is None or sys.stdin.isatty():
            _cache = {}
        else:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
            _cache = data if isinstance(data, dict) else {}
    return dict(_cache)


def caller() -> str:
    """Nome da máquina que pediu a execução."""
    return os.environ.get("RELAY_CALLER", "")


def detach(argv: list[str]) -> str:
    """Inicia um programa que continua vivo depois que o comando acabar.

    O agente mata o grupo de processos do comando quando ele termina. Pra um
    aplicativo continuar aberto, ele precisa sair desse grupo de propósito.
    Com o systemd do usuário, o programa vira uma unidade própria (sai até do
    cgroup e das regras do agente). Senão, vira uma sessão nova com stdin,
    stdout e stderr desligados.

    Devolve como abriu: "systemd" ou "direto". Pra links e arquivos, prefira
    open_uri(), que pede pro desktop abrir.
    """
    if not argv:
        raise ValueError("detach() precisa de um comando")
    runner = shutil.which("systemd-run")
    if runner:
        cmd = [
            runner,
            "--user",
            "--collect",
            "--quiet",
            # Muito lançador (xdg-open, gio open) abre o programa e sai na hora.
            # Com KillMode=process, o systemd não mata o que ficou rodando.
            "--property=KillMode=process",
            f"--unit=relay-detached-{secrets.token_hex(4)}",
        ]
        cmd += [f"--setenv={k}={os.environ[k]}" for k in SESSION_KEYS if k in os.environ]
        cmd += ["--", *argv]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
            if r.returncode == 0:
                return "systemd"
            print(f"systemd-run falhou ({r.stderr.strip()[:200]}), tentando abrir direto", file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"systemd-run falhou ({e}), tentando abrir direto", file=sys.stderr)
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return "direto"


def _open_via_portal(uri: str) -> bool:
    """Pede pro portal do desktop (xdg-desktop-portal) abrir o link.

    Quem abre é a própria sessão gráfica, com o programa padrão, fora do agente.
    Funciona com navegador em snap e com o navegador que já estava aberto.
    """
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.OpenURI",
            "OpenURI",
            GLib.Variant("(ssa{sv})", ("", uri, {})),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            15000,
            None,
        )
    except GLib.Error as e:
        print(f"o portal do desktop não abriu ({e.message}), tentando outro jeito", file=sys.stderr)
        return False
    return True


def open_uri(uri: str) -> str:
    """Abre um link (ou arquivo) com o programa padrão desta máquina, na tela dela.

    Tenta o portal do desktop primeiro. Se não der, usa o xdg-open fora do
    agente (detach). Devolve como abriu: "portal", "systemd" ou "direto".
    """
    if _open_via_portal(uri):
        return "portal"
    return detach(["xdg-open", uri])
