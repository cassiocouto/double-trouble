"""Formato do v1: uma linha JSON pra lá, uma pra cá, um segredo global.

Fica aqui por três motivos. A migração precisa ler o config.json antigo. O
agente v2 reconhece um cliente v1 e devolve um erro claro de versão. E na
fase 1 o pacote inteiro falava v1, pra provar que a troca de estrutura não
mudou o comportamento.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import socket
from pathlib import Path

from .errors import ConfigError, ProtocolError
from .safety import read_private_text

V1_PROTOCOL = 1


def canonical(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def compute_mac(key: bytes, kind: str, obj: dict) -> str:
    return hmac.new(key, kind.encode() + b"\n" + canonical(obj), hashlib.sha256).hexdigest()


def sign(key: bytes, kind: str, obj: dict) -> dict:
    obj["mac"] = compute_mac(key, kind, obj)
    return obj


def verify(key: bytes, kind: str, obj: dict) -> bool:
    mac = obj.get("mac")
    # O v1 comparava str com str e estourava com texto não-ASCII. Aqui é bytes.
    if not isinstance(mac, str) or len(mac) != 64 or not mac.isascii():
        return False
    try:
        expected = compute_mac(key, kind, obj)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(mac.encode("ascii"), expected.encode("ascii"))


def _reject_constant(name: str):
    raise ValueError(f"constante {name} não é permitida")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("número fora do intervalo")
    return value


def loads_strict(raw: bytes | str) -> object:
    """json.loads sem NaN, sem Infinity e sem chaves repetidas."""

    def no_dupes(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                # Não repete o nome da chave: ele vem de fora e pode ser enorme.
                raise ValueError("chave repetida")
            out[k] = v
        return out

    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float, object_pairs_hook=no_dupes)


def send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall(json.dumps(obj, ensure_ascii=True).encode() + b"\n")


def recv_line(sock: socket.socket, limit: int) -> dict:
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise ProtocolError("mensagem grande demais")
    line = bytes(buf).split(b"\n", 1)[0]
    if not line:
        raise ProtocolError("conexão fechada sem mensagem")
    try:
        obj = loads_strict(line)
    except (ValueError, RecursionError) as e:
        raise ProtocolError(f"mensagem não é JSON válido ({e})") from None
    if not isinstance(obj, dict):
        raise ProtocolError("mensagem não é um objeto JSON")
    return obj


def load_v1_config(path: Path) -> dict:
    """Lê o config.json do v1, exigindo modo 600."""
    text = read_private_text(path, "o config do v1")
    try:
        cfg = loads_strict(text)
    except ValueError as e:
        raise ConfigError(f"{path} não é JSON válido ({e})") from None
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} não é um objeto JSON")
    try:
        key = bytes.fromhex(cfg["segredo"])
    except (KeyError, ValueError, TypeError):
        raise ConfigError("o segredo no config do v1 não é hexadecimal válido") from None
    if len(key) < 32:
        raise ConfigError("o segredo do v1 tem menos de 32 bytes")
    cfg["_key"] = key
    cfg.setdefault("nome", socket.gethostname())
    cfg.setdefault("escutar", "127.0.0.1")
    cfg.setdefault("porta", 47800)
    cfg.setdefault("permitidos", ["100.64.0.0/10", "fd7a:115c:a1e0::/48", "127.0.0.0/8", "::1/128"])
    cfg.setdefault("maquinas", {})
    return cfg
