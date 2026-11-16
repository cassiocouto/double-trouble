"""Migração do ponte.py (v1) pro Relay.

O que faz
  * acha o ponte.service antigo (o que roda "ponte.py servir"), para e desabilita
  * faz backup do config.json do v1 em ~/.config/ponte (o original fica onde está)
  * cria ~/.config/relay/relay.json com o mesmo nome, porta, máquinas e segredo
  * COPIA os comandos de ~/.config/ponte/comandos pra ~/.config/relay/comandos
    (os originais ficam intactos; um comando que já existe na pasta nova não é tocado)

Nada é apagado. Pra voltar pro v1: systemctl --user enable --now ponte.service
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core import config as config_mod
from ..core import legacy, paths
from ..core.errors import ConfigError
from ..core.safety import write_private

V1_UNIT = "ponte.service"
LOOPBACK = {"127.0.0.0/8", "::1/128"}


def systemctl_binary() -> str:
    return os.environ.get("RELAY_SYSTEMCTL") or "systemctl"


def user_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def find_v1_unit() -> Path | None:
    """O ponte.service do v1 é o que tem ExecStart com 'ponte.py' e 'servir'."""
    unit = user_unit_dir() / V1_UNIT
    try:
        text = unit.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ExecStart=") and "ponte.py" in line and "servir" in line:
            return unit
    return None


@dataclass
class Plan:
    v1_config: Path
    v1_unit: Path | None
    new: config_mod.Config
    notes: list[str] = field(default_factory=list)


def _in_tailnet(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n) for n in config_mod.TAILSCALE_NETS)


def make_plan() -> Plan:
    v1_path = paths.legacy_config_file()
    if not v1_path.exists():
        raise ConfigError(f"não achei o config do v1 em {v1_path}")
    old = legacy.load_v1_config(v1_path)
    key: bytes = old["_key"]
    notes: list[str] = []

    name = config_mod.normalize_machine_name(str(old.get("nome", "")))
    config_mod.check_machine_name(name, "o nome desta máquina no v1")
    cfg = config_mod.Config(name=name)

    listen = str(old.get("escutar", "tailscale"))
    if _in_tailnet(listen):
        cfg.agent.listen = "tailscale"
        notes.append(f'escutar: {listen} vira "tailscale" (o agente descobre o IP sozinho)')
    elif listen in ("127.0.0.1", "::1"):
        cfg.agent.listen = "tailscale"
        notes.append("o v1 escutava só em 127.0.0.1. O v2 vai escutar no IP do Tailscale")
    else:
        cfg.agent.listen = listen
    port = old.get("porta", config_mod.DEFAULT_PORT)
    if isinstance(port, int) and 0 < port < 65535:
        cfg.agent.port = port
        cfg.agent.pairing_port = port + 1

    allowed = [str(n) for n in old.get("permitidos", [])]
    kept = [n for n in allowed if n not in LOOPBACK]
    if kept != allowed:
        notes.append("127.0.0.0/8 e ::1 saíram dos permitidos (não servem pro Tailscale)")
    cfg.agent.allowed = kept or list(config_mod.TAILSCALE_NETS)

    machines = old.get("maquinas", {})
    if not isinstance(machines, dict):
        machines = {}
    for raw_name, m in machines.items():
        mname = config_mod.normalize_machine_name(str(raw_name))
        try:
            config_mod.check_machine_name(mname)
        except ConfigError:
            notes.append(f"pulei a máquina '{raw_name}' (nome inválido no v2)")
            continue
        if mname == name:
            continue
        m = m if isinstance(m, dict) else {}
        address = str(m.get("endereco") or mname)
        mport = m.get("porta", config_mod.DEFAULT_PORT)
        if not isinstance(mport, int) or not 0 < mport < 65536:
            mport = config_mod.DEFAULT_PORT
        cfg.machines[mname] = config_mod.MachineEntry(name=mname, address=address, port=mport, secret=key)
        cfg.agent.controllers[mname] = config_mod.ControllerEntry(name=mname, secret=key)
    if cfg.machines:
        notes.append(
            "o segredo do v1 vale nas duas direções. Funciona, mas o ideal é parear de novo "
            "(relay parear) pra ter um segredo por direção"
        )
    config_mod.Config.from_json(cfg.to_json())
    return Plan(v1_config=v1_path, v1_unit=find_v1_unit(), new=cfg, notes=notes)


def describe(plan: Plan) -> list[str]:
    target = paths.config_file()
    lines = [
        f"config do v1        {plan.v1_config} (fica intacto, e ganha um backup)",
        f"config novo         {target}",
        f"esta máquina        {plan.new.name}, porta {plan.new.agent.port}, escutar {plan.new.agent.listen}",
        f"máquinas            {', '.join(plan.new.machines) or 'nenhuma'}",
        f"comandos            copiados de {paths.legacy_commands_dir()} pra {paths.commands_dir()}"
        " (os originais ficam)",
    ]
    if plan.v1_unit:
        lines.append(f"serviço do v1       {plan.v1_unit} vai ser parado e desabilitado (o arquivo fica)")
    else:
        lines.append("serviço do v1       não achei (nada pra parar)")
    lines += [f"nota                {n}" for n in plan.notes]
    return lines


def copy_commands() -> list[str]:
    """Copia os .py da pasta do v1, com modo 600. Não sobrescreve nada na pasta nova."""
    source = paths.legacy_commands_dir()
    target = paths.commands_dir()
    if not source.is_dir():
        return []
    paths.ensure_private_dir(target)
    copied = []
    for item in sorted(source.glob("*.py")):
        if item.is_symlink() or not item.is_file():
            continue
        dest = target / item.name
        if dest.exists():
            continue
        write_private(dest, item.read_bytes())
        copied.append(item.stem)
    return copied


def apply(plan: Plan, force: bool = False, stop_v1: bool = True) -> list[str]:
    done = []
    target = paths.config_file()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if target.exists():
        if not force:
            raise ConfigError(f"{target} já existe. Use --forcar (o atual vira backup)")
        backup_v2 = target.with_name(f"{target.name}.bak-{stamp}")
        write_private(backup_v2, target.read_bytes())
        done.append(f"backup do config v2 anterior em {backup_v2}")
    backup = plan.v1_config.with_name(f"{plan.v1_config.name}.v1-backup-{stamp}")
    write_private(backup, plan.v1_config.read_bytes())
    done.append(f"backup do config v1 em {backup}")
    config_mod.save(plan.new, target)
    done.append(f"config novo criado em {target}")
    copied = copy_commands()
    if copied:
        done.append(f"comandos copiados pra {paths.commands_dir()}: {', '.join(copied)}")
    if plan.v1_unit and stop_v1:
        for action in ("stop", "disable"):
            try:
                r = subprocess.run(
                    [systemctl_binary(), "--user", action, V1_UNIT],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                done.append(f"aviso: não consegui rodar systemctl --user {action} {V1_UNIT} ({e})")
                continue
            if r.returncode == 0:
                done.append(f"systemctl --user {action} {V1_UNIT}: ok")
            else:
                done.append(f"aviso: systemctl --user {action} {V1_UNIT} falhou ({r.stderr.strip()})")
    return done
