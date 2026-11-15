"""Conversa com o comando `tailscale` (IP desta máquina e `whois` de quem chama)."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import threading
import time

from .errors import AuthError

TAILSCALE_RANGES = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]


def binary() -> str | None:
    """Caminho do `tailscale`. PONTE_TAILSCALE troca o binário (usado nos testes)."""
    override = os.environ.get("PONTE_TAILSCALE")
    if override:
        return override if os.access(override, os.X_OK) else None
    return shutil.which("tailscale")


def _run(args: list[str], timeout: float = 5) -> str | None:
    exe = binary()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def own_ip(version: int = 4) -> str | None:
    out = _run(["ip", f"-{version}"])
    if not out:
        return None
    for token in out.split():
        try:
            return str(ipaddress.ip_address(token))
        except ValueError:
            continue
    return None


def in_tailnet(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in TAILSCALE_RANGES)


def node_fqdn(whois: dict) -> str | None:
    """O nome MagicDNS do nó (Node.Name), em minúsculo e sem o ponto final.

    Só vale o Node.Name, que quem dá é o servidor de controle do Tailscale (e
    é único no tailnet). O Hostinfo.Hostname e o ComputedName podem vir do que
    o próprio aparelho diz sobre si, então não servem pra provar nada.
    """
    node = whois.get("Node") if isinstance(whois, dict) else None
    if not isinstance(node, dict):
        return None
    name = node.get("Name")
    if not isinstance(name, str) or not name.strip("."):
        return None
    return name.rstrip(".").lower()


def own_suffix() -> str | None:
    """Domínio MagicDNS deste tailnet (ex.: tail1234.ts.net), lido do `tailscale status --json`."""
    out = _run(["status", "--json"])
    if not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    me = data.get("Self") if isinstance(data, dict) else None
    dns = me.get("DNSName") if isinstance(me, dict) else None
    if not isinstance(dns, str) or "." not in dns.rstrip("."):
        return None
    return dns.rstrip(".").lower().split(".", 1)[1]


def whois(ip: str) -> dict | None:
    out = _run(["whois", "--json", ip.split("%", 1)[0]])
    if not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class WhoisChecker:
    """Confere que o IP do pedido é do nó esperado (camada extra, opcional).

    modo "auto"    confere quando o `tailscale` existe e o IP é do tailnet
    modo "sempre"  sempre confere; sem `tailscale` o pedido é recusado
    modo "nunca"   não confere
    """

    def __init__(self, ttl: float = 60):
        self.ttl = ttl
        self._cache: dict[str, tuple[float, str | None]] = {}
        self._own: tuple[float, str | None] | None = None
        self._lock = threading.Lock()

    def _fqdn(self, ip: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(ip)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        data = whois(ip)
        fqdn = node_fqdn(data) if data else None
        with self._lock:
            if len(self._cache) > 1000:
                self._cache.clear()
            self._cache[ip] = (now, fqdn)
        return fqdn

    def _suffix(self) -> str | None:
        now = time.monotonic()
        if self._own is not None and now - self._own[0] < self.ttl:
            return self._own[1]
        suffix = own_suffix()
        self._own = (now, suffix)
        return suffix

    def check(self, mode: str, ip: str, expected: str) -> None:
        if mode == "nunca":
            return
        if mode == "auto" and (not binary() or not in_tailnet(ip)):
            return
        if not binary():
            raise AuthError("verificar_tailscale = sempre, mas o comando tailscale não foi achado")
        fqdn = self._fqdn(ip)
        if fqdn is None:
            raise AuthError(f"o tailscale whois não reconheceu o IP {ip}")
        suffix = self._suffix()
        if suffix is None:
            raise AuthError("não consegui ler o domínio deste tailnet (tailscale status --json)")
        wanted = f"{expected.lower()}.{suffix}"
        if fqdn != wanted:
            raise AuthError(f"o IP {ip} é do nó '{fqdn}', não de '{wanted}'")
