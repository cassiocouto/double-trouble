"""Onde cada coisa fica no disco.

Tudo é função (e não constante) pra os testes poderem trocar as pastas pelo
ambiente. Se RELAY_DIR estiver definido e RELAY_STATE_DIR / RELAY_DATA_DIR
não, estado e dados ficam dentro dele. Assim uma variável só isola tudo.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def base_dir() -> Path:
    """Pasta de config. Padrão ~/.config/relay."""
    return _env_path("RELAY_DIR") or Path.home() / ".config" / "relay"


def commands_dir() -> Path:
    return base_dir() / "comandos"


def config_file() -> Path:
    """Config do Relay."""
    return base_dir() / "relay.json"


def state_dir() -> Path:
    """Estado do agente (auditoria). Padrão ~/.local/state/relay."""
    explicit = _env_path("RELAY_STATE_DIR")
    if explicit:
        return explicit
    if os.environ.get("RELAY_DIR"):
        return base_dir() / "estado"
    return Path.home() / ".local" / "state" / "relay"


def data_dir() -> Path:
    """Dados da interface (histórico). Padrão ~/.local/share/relay."""
    explicit = _env_path("RELAY_DATA_DIR")
    if explicit:
        return explicit
    if os.environ.get("RELAY_DIR"):
        return base_dir() / "dados"
    return Path.home() / ".local" / "share" / "relay"


def ensure_private_dir(path: Path) -> Path:
    """Cria a pasta com modo 700 se não existir. Não mexe no modo de uma existente."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
