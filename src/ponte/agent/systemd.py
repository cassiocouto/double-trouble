"""sd_notify feito na mão: uma mensagem num socket unix do tipo datagrama.

Sem dependência externa. Se NOTIFY_SOCKET não existir (rodando fora do
systemd, ou nos testes), as funções não fazem nada.
"""

from __future__ import annotations

import os
import socket


def notify(*items: str) -> bool:
    """Manda READY=1, STATUS=..., WATCHDOG=1, STOPPING=1 etc. pro systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not items:
        return False
    if addr.startswith("@"):
        target = "\0" + addr[1:]  # socket abstrato
    elif addr.startswith("/"):
        target = addr
    else:
        return False  # vsock e afins não são usados num serviço de usuário
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(target)
            s.sendall("\n".join(items).encode("utf-8"))
        return True
    except OSError:
        return False


def watchdog_interval() -> float | None:
    """De quanto em quanto tempo mandar WATCHDOG=1 (metade do WatchdogSec)."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None
    try:
        value = int(usec)
    except ValueError:
        return None
    return value / 1_000_000 / 2 if value > 0 else None
