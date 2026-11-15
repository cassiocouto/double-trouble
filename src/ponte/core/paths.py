"""Onde cada coisa fica no disco.

Tudo é função (e não constante) pra os testes poderem trocar as pastas pelo
ambiente. Se PONTE_DIR estiver definido e PONTE_STATE_DIR / PONTE_DATA_DIR
não, estado e dados ficam dentro dele. Assim uma variável só isola tudo.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def base_dir() -> Path:
    """Pasta de config. Padrão ~/.config/ponte, a mesma do v1."""
    return _env_path("PONTE_DIR") or Path.home() / ".config" / "ponte"


def commands_dir() -> Path:
    return base_dir() / "comandos"


def config_file() -> Path:
    """Config do v2. O v1 usava config.json, que a migração deixa intacto."""
    return base_dir() / "ponte.json"


def legacy_config_file() -> Path:
    return base_dir() / "config.json"


def state_dir() -> Path:
    """Estado do agente (auditoria). Padrão ~/.local/state/ponte."""
    explicit = _env_path("PONTE_STATE_DIR")
    if explicit:
        return explicit
    if os.environ.get("PONTE_DIR"):
        return base_dir() / "estado"
    return Path.home() / ".local" / "state" / "ponte"


def data_dir() -> Path:
    """Dados da interface (histórico). Padrão ~/.local/share/ponte."""
    explicit = _env_path("PONTE_DATA_DIR")
    if explicit:
        return explicit
    if os.environ.get("PONTE_DIR"):
        return base_dir() / "dados"
    return Path.home() / ".local" / "share" / "ponte"


def ensure_private_dir(path: Path) -> Path:
    """Cria a pasta com modo 700 se não existir. Não mexe no modo de uma existente."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
