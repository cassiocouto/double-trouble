"""Config do v2 em ~/.config/ponte/ponte.json (modo 600).

Duas metades no mesmo arquivo, porque a mesma máquina pode ter os dois papéis.

  agente.controladores   quem pode mandar comandos pra esta máquina
  maquinas               pra quem esta máquina manda comandos

Cada direção (controlador -> agente) tem seu próprio segredo. Revogar uma
direção não mexe na outra, e quem rouba um segredo só ganha aquela direção.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import json
import os
import re
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .errors import ConfigError
from .legacy import loads_strict
from .safety import read_private_text, write_private

CONFIG_VERSION = 2
DEFAULT_PORT = 47800
TAILSCALE_NETS = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")
KNOWN_MACHINES = ("cass-dell-brasil", "cass-dell-g15-5511")
MIN_SECRET_BYTES = 32
MACHINE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
VERIFY_MODES = ("auto", "sempre", "nunca")


def normalize_machine_name(name: str) -> str:
    """Hostname vira nome de máquina: minúsculo e só o primeiro rótulo."""
    return name.strip().lower().split(".", 1)[0]


def check_machine_name(name: object, what: str = "nome de máquina") -> str:
    if not isinstance(name, str) or not MACHINE_NAME_RE.fullmatch(name):
        raise ConfigError(f"{what} inválido: {name!r} (use letras minúsculas, números e '-')")
    return name


def parse_secret(value: object, what: str) -> bytes:
    if not isinstance(value, str):
        raise ConfigError(f"o segredo de {what} precisa ser texto hexadecimal")
    try:
        raw = bytes.fromhex(value.strip())
    except ValueError:
        raise ConfigError(f"o segredo de {what} não é hexadecimal válido") from None
    if len(raw) < MIN_SECRET_BYTES:
        raise ConfigError(f"o segredo de {what} precisa ter pelo menos {MIN_SECRET_BYTES} bytes")
    return raw


@dataclass
class ControllerEntry:
    """Uma máquina que pode mandar comandos pra esta."""

    name: str
    secret: bytes
    commands: list[str] | None = None  # None = todos
    tailscale_node: str | None = None  # nome esperado no `tailscale whois`; None = o próprio nome

    def allows(self, command: str) -> bool:
        return self.commands is None or command in self.commands

    def to_json(self) -> dict:
        out: dict = {
            "segredo": self.secret.hex(),
            "comandos": "*" if self.commands is None else self.commands,
        }
        if self.tailscale_node:
            out["no_tailscale"] = self.tailscale_node
        return out


@dataclass
class MachineEntry:
    """Uma máquina que esta controla."""

    name: str
    address: str
    port: int
    secret: bytes

    def to_json(self) -> dict:
        return {"endereco": self.address, "porta": self.port, "segredo": self.secret.hex()}


@dataclass
class AgentSettings:
    listen: str = "tailscale"  # "tailscale" ou um IP
    port: int = DEFAULT_PORT
    pairing_port: int = DEFAULT_PORT + 1
    allowed: list[str] = field(default_factory=lambda: list(TAILSCALE_NETS))
    verify_tailscale: str = "auto"
    max_parallel: int = 4
    controllers: dict[str, ControllerEntry] = field(default_factory=dict)

    def networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [ipaddress.ip_network(n, strict=False) for n in self.allowed]


@dataclass
class Config:
    name: str
    agent: AgentSettings = field(default_factory=AgentSettings)
    machines: dict[str, MachineEntry] = field(default_factory=dict)

    # -- serialização -----------------------------------------------------
    def to_json(self) -> dict:
        a = self.agent
        return {
            "versao": CONFIG_VERSION,
            "nome": self.name,
            "agente": {
                "escutar": a.listen,
                "porta": a.port,
                "porta_pareamento": a.pairing_port,
                "permitidos": a.allowed,
                "verificar_tailscale": a.verify_tailscale,
                "max_paralelo": a.max_parallel,
                "controladores": {n: c.to_json() for n, c in sorted(a.controllers.items())},
            },
            "maquinas": {n: m.to_json() for n, m in sorted(self.machines.items())},
        }

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, data: object) -> Config:
        if not isinstance(data, dict):
            raise ConfigError("o config precisa ser um objeto JSON")
        version = data.get("versao")
        if version != CONFIG_VERSION:
            raise ConfigError(f"versão de config {version!r} não suportada (esperava {CONFIG_VERSION})")
        name = check_machine_name(data.get("nome"), "o 'nome' desta máquina")
        a = data.get("agente", {})
        if not isinstance(a, dict):
            raise ConfigError("'agente' precisa ser um objeto")
        agent = AgentSettings()
        agent.listen = _listen(a.get("escutar", agent.listen))
        agent.port = _port(a.get("porta", agent.port), "porta")
        agent.pairing_port = _port(a.get("porta_pareamento", agent.port + 1), "porta_pareamento")
        allowed = a.get("permitidos", agent.allowed)
        if not isinstance(allowed, list) or not all(isinstance(n, str) for n in allowed):
            raise ConfigError("'permitidos' precisa ser uma lista de redes, como 100.64.0.0/10")
        try:
            for n in allowed:
                ipaddress.ip_network(n, strict=False)
        except ValueError as e:
            raise ConfigError(f"rede inválida em 'permitidos' ({e})") from None
        agent.allowed = list(allowed)
        mode = a.get("verificar_tailscale", agent.verify_tailscale)
        if mode not in VERIFY_MODES:
            raise ConfigError(f"'verificar_tailscale' precisa ser um de {', '.join(VERIFY_MODES)}")
        agent.verify_tailscale = mode
        mp = a.get("max_paralelo", agent.max_parallel)
        if not isinstance(mp, int) or isinstance(mp, bool) or not 1 <= mp <= 64:
            raise ConfigError("'max_paralelo' precisa ser um inteiro de 1 a 64")
        agent.max_parallel = mp
        ctrls = a.get("controladores", {})
        if not isinstance(ctrls, dict):
            raise ConfigError("'controladores' precisa ser um objeto")
        for cname, c in ctrls.items():
            check_machine_name(cname, "nome de controlador")
            if not isinstance(c, dict):
                raise ConfigError(f"o controlador '{cname}' precisa ser um objeto")
            cmds = c.get("comandos", "*")
            if cmds == "*":
                cmds = None
            elif not isinstance(cmds, list) or not all(isinstance(x, str) for x in cmds):
                raise ConfigError(f"'comandos' de '{cname}' precisa ser \"*\" ou uma lista de nomes")
            node = c.get("no_tailscale")
            if node is not None:
                node = check_machine_name(node, f"'no_tailscale' de '{cname}'")
            agent.controllers[cname] = ControllerEntry(
                name=cname,
                secret=parse_secret(c.get("segredo"), f"'{cname}'"),
                commands=cmds,
                tailscale_node=node,
            )
        machines = {}
        ms = data.get("maquinas", {})
        if not isinstance(ms, dict):
            raise ConfigError("'maquinas' precisa ser um objeto")
        for mname, m in ms.items():
            check_machine_name(mname, "nome de máquina")
            if not isinstance(m, dict):
                raise ConfigError(f"a máquina '{mname}' precisa ser um objeto")
            address = m.get("endereco", mname)
            if not isinstance(address, str) or not address or len(address) > 253:
                raise ConfigError(f"'endereco' de '{mname}' inválido")
            machines[mname] = MachineEntry(
                name=mname,
                address=address,
                port=_port(m.get("porta", DEFAULT_PORT), f"porta de '{mname}'"),
                secret=parse_secret(m.get("segredo"), f"'{mname}'"),
            )
        return cls(name=name, agent=agent, machines=machines)

    # -- busca ------------------------------------------------------------
    def find_machine(self, query: str) -> MachineEntry:
        """Acha a máquina pelo nome inteiro ou por um pedaço (g15, brasil)."""
        if query in self.machines:
            return self.machines[query]
        hits = [n for n in self.machines if query.lower() in n]
        if not hits:
            known = ", ".join(self.machines) or "nenhuma"
            raise ConfigError(f"não conheço a máquina '{query}'. Conheço: {known}")
        if len(hits) > 1:
            raise ConfigError(f"'{query}' bate com mais de uma máquina ({', '.join(hits)})")
        return self.machines[hits[0]]


def _port(value: object, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 65535:
        raise ConfigError(f"'{what}' precisa ser um número de 0 a 65535")
    return value


def _listen(value: object) -> str:
    if value == "tailscale":
        return value
    if not isinstance(value, str):
        raise ConfigError("'escutar' precisa ser \"tailscale\" ou um IP")
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise ConfigError(f"'escutar' precisa ser \"tailscale\" ou um IP, veio {value!r}") from None
    if ip.is_unspecified:
        raise ConfigError("'escutar' não pode ser 0.0.0.0 nem ::, escolha o IP do Tailscale")
    return value


# --------------------------------------------------------------------------
# disco


def default_config(name: str | None = None) -> Config:
    return Config(name=normalize_machine_name(name or socket.gethostname()))


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    text = read_private_text(path, "o config")
    try:
        data = loads_strict(text)
    except (ValueError, RecursionError) as e:
        raise ConfigError(f"{path} não é JSON válido ({e})") from None
    return Config.from_json(data)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    paths.ensure_private_dir(path.parent)
    write_private(path, cfg.dumps())


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    paths.ensure_private_dir(path.parent)
    lock = path.parent / ".ponte.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def update(change: Callable[[Config], None], path: Path | None = None, create: bool = False) -> Config:
    """Lê, muda e grava o config com trava, pra CLI e interface não se atropelarem."""
    path = path or paths.config_file()
    with _locked(path):
        cfg = default_config() if create and not path.exists() else load(path)
        change(cfg)
        Config.from_json(json.loads(cfg.dumps()))  # confere antes de gravar
        save(cfg, path)
    return cfg


class ConfigSource:
    """Config que o agente recarrega sozinho quando o arquivo muda."""

    def __init__(self, path: Path | None = None, on_error: Callable[[str], None] | None = None):
        self.path = path or paths.config_file()
        self.on_error = on_error
        self._stamp: tuple | None = None
        self._cfg = load(self.path)
        self._stamp = self._read_stamp()

    def _read_stamp(self) -> tuple | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode, st.st_uid)

    def get(self) -> Config | None:
        """Config atual, ou None se o arquivo novo estiver quebrado ou inseguro.

        Falha fechada: com config ruim o agente recusa tudo até alguém
        consertar, em vez de seguir com segredos que podem ter vazado.
        """
        stamp = self._read_stamp()
        if stamp != self._stamp:
            self._stamp = stamp
            try:
                self._cfg = load(self.path)
                self.error = None
            except ConfigError as e:
                self._cfg = None
                self.error = str(e)
                if self.on_error:
                    self.on_error(f"config com problema, recusando pedidos até consertar ({e})")
        return self._cfg

    error: str | None = None
