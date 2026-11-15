"""Protocolo v2. A especificação completa está em docs/PROTOCOLO.md.

Resumo
  1. O cliente manda a linha mágica b"PONTE/2\\n". O agente responde a mesma.
     Versões diferentes param aqui com um erro claro.
  2. Depois disso tudo é frame: 4 bytes big-endian com o tamanho + JSON UTF-8.
  3. O cliente manda um frame de pedido. O agente manda um ou mais frames de
     resposta, numerados por `seq` a partir de 0. O último tem `fim: true`.
  4. Pedido e respostas são assinados com HMAC-SHA256 usando uma chave
     derivada do segredo da direção (controlador -> agente).
  5. Toda resposta carrega o nonce do pedido e o seq. O cliente recusa
     resposta sem assinatura, de outro pedido, ou fora de ordem.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import struct
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .errors import AuthError, ProtocolError
from .legacy import loads_strict

PROTOCOL = 2
MAGIC = b"PONTE/2\n"
MAGIC_PREFIX = b"PONTE/"
MAX_MAGIC = 16

MAX_REQUEST_FRAME = 64 * 1024
MAX_RESPONSE_FRAME = 1024 * 1024
MAX_CLOCK_SKEW = 60
NONCE_TTL = 3 * MAX_CLOCK_SKEW
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
MAC_RE = re.compile(r"^[0-9a-f]{64}$")

KIND_REQUEST = b"ponte/2 pedido\n"
KIND_RESPONSE = b"ponte/2 resposta\n"

UNAUTHENTICATED = "assinatura inválida ou máquina desconhecida"

_HEADER = struct.Struct(">I")


# --------------------------------------------------------------------------
# chaves e assinatura


def derive_key(secret: bytes, controller: str, agent: str) -> bytes:
    """Chave da direção controlador -> agente.

    Mesmo que alguém use o mesmo segredo nas duas direções (config migrado do
    v1), a chave de A -> B é diferente da de B -> A. Um pedido de uma direção
    não serve na outra.
    """
    label = json.dumps(["ponte/2", controller, agent], ensure_ascii=True).encode()
    return hmac.new(secret, label, hashlib.sha256).digest()


def canonical(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def compute_mac(key: bytes, kind: bytes, obj: dict) -> str:
    return hmac.new(key, kind + canonical(obj), hashlib.sha256).hexdigest()


def sign(key: bytes, kind: bytes, obj: dict) -> dict:
    obj["mac"] = compute_mac(key, kind, obj)
    return obj


def verify(key: bytes, kind: bytes, obj: dict) -> bool:
    mac = obj.get("mac")
    if not isinstance(mac, str) or not MAC_RE.fullmatch(mac):
        return False
    try:
        expected = compute_mac(key, kind, obj)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(mac.encode("ascii"), expected.encode("ascii"))


def new_nonce() -> str:
    return secrets.token_hex(16)


# --------------------------------------------------------------------------
# frames


def encode_frame(obj: dict) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _HEADER.pack(len(payload)) + payload


def send_frame(sock: socket.socket, obj: dict, limit: int = MAX_RESPONSE_FRAME) -> None:
    data = encode_frame(obj)
    if len(data) - _HEADER.size > limit:
        raise ProtocolError("frame grande demais pra mandar")
    sock.sendall(data)


def _arm(sock: socket.socket, deadline: float | None) -> None:
    """Com prazo total, cada recv só espera o que sobra dele (contra conexão lenta de propósito)."""
    if deadline is None:
        return
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("prazo pra mandar a mensagem acabou")
    sock.settimeout(left)


def _recv_exact(sock: socket.socket, n: int, deadline: float | None = None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        _arm(sock, deadline)
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ProtocolError("conexão fechada no meio de uma mensagem" if buf else "conexão fechada")
        buf += chunk
    return bytes(buf)


def recv_frame(sock: socket.socket, limit: int, deadline: float | None = None) -> dict:
    """Lê um frame. O tamanho é conferido antes de ler o corpo (invariante 10).

    Com `deadline` (time.monotonic()), o frame inteiro precisa chegar até lá.
    """
    (size,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size, deadline))
    if size == 0:
        raise ProtocolError("frame vazio")
    if size > limit:
        raise ProtocolError(f"frame grande demais ({size} bytes, limite {limit})")
    raw = _recv_exact(sock, size, deadline)
    try:
        obj = loads_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ProtocolError(f"frame não é JSON válido ({e})") from None
    if not isinstance(obj, dict):
        raise ProtocolError("frame não é um objeto JSON")
    return obj


def read_magic(sock: socket.socket, deadline: float | None = None, size: int = MAX_MAGIC) -> bytes:
    """Lê a linha mágica byte a byte (no máximo `size` bytes)."""
    buf = bytearray()
    while len(buf) < size:
        _arm(sock, deadline)
        b = sock.recv(1)
        if not b:
            break
        buf += b
        if b == b"\n" or (len(buf) == 1 and b == b"{"):
            break
    return bytes(buf)


# --------------------------------------------------------------------------
# pedidos (lado do cliente)


def build_request(key: bytes, controller: str, agent: str, action: str, **fields) -> dict:
    req = {
        "v": PROTOCOL,
        "de": controller,
        "para": agent,
        "ts": int(time.time()),
        "nonce": new_nonce(),
        "acao": action,
        **fields,
    }
    return sign(key, KIND_REQUEST, req)


@dataclass
class ResponseVerifier:
    """Confere cada frame de resposta de um pedido (invariante 4)."""

    key: bytes
    controller: str
    agent: str
    nonce: str
    next_seq: int = 0
    done: bool = False

    def check(self, frame: dict) -> dict:
        if self.done:
            raise AuthError("chegou mensagem depois do fim da resposta")
        if "mac" not in frame:
            # Erro sem assinatura: só pode ser recusa do agente antes de autenticar.
            # Serve pra mostrar o motivo, nunca como resposta de verdade.
            msg = frame.get("erro")
            msg = msg[:300] if isinstance(msg, str) else "sem detalhe"
            raise AuthError(f"a outra máquina recusou sem assinar ({msg})")
        if not verify(self.key, KIND_RESPONSE, frame):
            raise AuthError("resposta com assinatura inválida")
        if frame.get("v") != PROTOCOL:
            raise AuthError("resposta de outra versão do protocolo")
        if frame.get("nonce") != self.nonce:
            raise AuthError("resposta de outro pedido (nonce diferente)")
        if frame.get("de") != self.agent or frame.get("para") != self.controller:
            raise AuthError("resposta com origem ou destino errado")
        seq = frame.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != self.next_seq:
            raise AuthError(f"resposta fora de ordem (esperava {self.next_seq}, veio {seq!r})")
        self.next_seq += 1
        if frame.get("fim") is True:
            self.done = True
        return frame


# --------------------------------------------------------------------------
# respostas (lado do agente)


@dataclass
class Responder:
    """Manda frames assinados e numerados pra um pedido autenticado."""

    sock: socket.socket
    key: bytes
    agent: str
    controller: str
    nonce: str
    seq: int = 0
    closed: bool = False

    def send(self, tipo: str, fim: bool = False, **fields) -> None:
        if self.closed:
            raise ProtocolError("resposta já terminou")
        body = {
            "v": PROTOCOL,
            "de": self.agent,
            "para": self.controller,
            "nonce": self.nonce,
            "seq": self.seq,
            "tipo": tipo,
            "fim": fim,
            **fields,
        }
        sign(self.key, KIND_RESPONSE, body)
        send_frame(self.sock, body)
        self.seq += 1
        if fim:
            self.closed = True

    def error(self, message: str, code: str = "erro") -> None:
        self.send("erro", fim=True, erro=message, codigo_erro=code)


def unsigned_error(message: str) -> dict:
    """Erro pra quem não se autenticou. Nunca leva assinatura (invariante 3)."""
    return {"v": PROTOCOL, "tipo": "erro", "fim": True, "autenticado": False, "erro": message}


@dataclass
class NonceCache:
    ttl: float = NONCE_TTL
    max_size: int = 100_000
    _seen: OrderedDict = field(default_factory=OrderedDict)

    def __post_init__(self):
        import threading

        self._lock = threading.Lock()

    def add(self, nonce: str, now: float | None = None) -> bool:
        """False se o nonce já foi visto (replay) ou se o cache lotou."""
        now = time.time() if now is None else now
        with self._lock:
            while self._seen:
                _oldest, t = next(iter(self._seen.items()))
                if now - t <= self.ttl:
                    break
                self._seen.popitem(last=False)
            if nonce in self._seen or len(self._seen) >= self.max_size:
                return False
            self._seen[nonce] = now
            return True
