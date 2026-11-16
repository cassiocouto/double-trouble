"""Mostra nome, tempo ligada, carga, memória e disco desta máquina."""

import os
import shutil
import socket

ICONE = "computer-symbolic"
CATEGORIA = "Sistema"
TIMEOUT = 15


def uptime() -> str:
    with open("/proc/uptime") as f:
        secs = int(float(f.read().split()[0]))
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h {rest // 60}min"


def memory() -> str:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
    total = info["MemTotal"] / 2**20
    free = info["MemAvailable"] / 2**20
    return f"{total - free:.1f} de {total:.1f} GiB em uso"


disk = shutil.disk_usage(os.path.expanduser("~"))
print(f"máquina  {socket.gethostname()}")
print(f"ligada   {uptime()}")
print(f"carga    {' '.join(f'{x:.2f}' for x in os.getloadavg())}")
print(f"memória  {memory()}")
print(f"disco    {disk.used / 2**30:.0f} de {disk.total / 2**30:.0f} GiB em uso na home")
