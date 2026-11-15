"""SPAKE2 (estilo RFC 9382) sobre o grupo MODP de 2048 bits da RFC 3526.

Pra que serve: as duas máquinas conhecem um código curto (8 dígitos) e
precisam sair com um segredo forte e igual, sem mandar o código nem o
segredo pela rede. Quem escuta a conversa não aprende nada que deixe testar
códigos depois. Quem finge ser uma das pontas só consegue testar UM código
por tentativa, e o agente fecha a janela depois de 3 erros.

Papéis: A é o controlador (quem digita o código), B é o agente (quem mostra).

  w = H(código) mod q
  A: X = G^x · M^w            B: Y = G^y · N^w
  A: K = (Y / N^w)^x          B: K = (X / M^w)^y
  TT = id_A, id_B, X, Y, K, w (cada um com o tamanho na frente)
  Ke || Ka = SHA-512(TT)
  confirmações: HMAC(HMAC(Ka, "confirma A"), TT) e o mesmo com "confirma B"

M e N são elementos do grupo tirados de um hash (ninguém conhece o log
discreto deles). Feito à mão, só com a biblioteca padrão. Não é auditado e
não é tempo constante; pra um pareamento que acontece de vez em quando,
dentro do Tailscale, isso é aceitável (ver docs/SEGURANCA.md).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# RFC 3526, grupo 14 (2048 bits). p é primo seguro: q = (p - 1) / 2 também é primo.
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
Q = (P - 1) // 2
G = 4  # 2², um quadrado, então gera o subgrupo de ordem q
ELEMENT_BYTES = 256


def _hash_to_group(label: bytes) -> int:
    """Elemento do subgrupo de ordem q, sem log discreto conhecido."""
    counter = 0
    while True:
        data = hashlib.shake_256(b"ponte/2 spake2 " + label + counter.to_bytes(4, "big")).digest(ELEMENT_BYTES + 32)
        e = pow(int.from_bytes(data, "big") % P, 2, P)
        if e not in (0, 1, P - 1):
            return e
        counter += 1  # pragma: no cover - probabilidade desprezível


M = _hash_to_group(b"M")
N = _hash_to_group(b"N")


def is_element(e: object) -> bool:
    """Confere que e é um elemento válido (e não trivial) do subgrupo de ordem q."""
    return isinstance(e, int) and not isinstance(e, bool) and 1 < e < P - 1 and pow(e, Q, P) == 1


def encode(e: int) -> str:
    return e.to_bytes(ELEMENT_BYTES, "big").hex()


def decode(text: object) -> int:
    if not isinstance(text, str) or len(text) != 2 * ELEMENT_BYTES:
        raise ValueError("elemento com tamanho errado")
    try:
        value = int(text, 16)
    except ValueError:
        raise ValueError("elemento não é hexadecimal") from None
    if not is_element(value):
        raise ValueError("elemento fora do grupo")
    return value


def password_scalar(code: str) -> int:
    digest = hashlib.sha512(b"ponte/2 spake2 w\x00" + code.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % Q or 1


def _framed(*parts: bytes) -> bytes:
    return b"".join(len(p).to_bytes(8, "little") + p for p in parts)


class Spake2:
    def __init__(self, role: str, code: str, id_a: str, id_b: str):
        if role not in ("A", "B"):
            raise ValueError("papel precisa ser A ou B")
        self.role = role
        self.id_a = id_a.encode("utf-8")
        self.id_b = id_b.encode("utf-8")
        self.w = password_scalar(code)
        self.x = secrets.randbelow(Q - 1) + 1
        mask = M if role == "A" else N
        self.element = (pow(G, self.x, P) * pow(mask, self.w, P)) % P
        self._ke: bytes | None = None
        self._confirm: dict[str, bytes] = {}

    def message(self) -> str:
        return encode(self.element)

    def finish(self, peer_text: object) -> None:
        peer = decode(peer_text)
        mask = N if self.role == "A" else M
        # mask tem ordem q, então mask^(q - w) = mask^(-w).
        base = (peer * pow(mask, Q - self.w, P)) % P
        k = pow(base, self.x, P)
        if k == 1:
            raise ValueError("chave trivial")
        x_el, y_el = (self.element, peer) if self.role == "A" else (peer, self.element)
        tt = _framed(
            self.id_a,
            self.id_b,
            x_el.to_bytes(ELEMENT_BYTES, "big"),
            y_el.to_bytes(ELEMENT_BYTES, "big"),
            k.to_bytes(ELEMENT_BYTES, "big"),
            self.w.to_bytes(ELEMENT_BYTES, "big"),
        )
        digest = hashlib.sha512(tt).digest()
        self._ke, ka = digest[:32], digest[32:]
        for side in ("A", "B"):
            kc = hmac.new(ka, b"confirma " + side.encode(), hashlib.sha256).digest()
            self._confirm[side] = hmac.new(kc, tt, hashlib.sha256).digest()

    def _need(self) -> None:
        if self._ke is None:
            raise RuntimeError("chame finish() antes")

    def my_confirmation(self) -> str:
        self._need()
        return self._confirm[self.role].hex()

    def check_peer(self, value: object) -> bool:
        self._need()
        other = "B" if self.role == "A" else "A"
        if not isinstance(value, str) or len(value) != 64:
            return False
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            return False
        return hmac.compare_digest(raw, self._confirm[other])

    def derive(self, label: str, size: int = 32) -> bytes:
        """Segredo derivado da chave combinada. Só use depois de conferir a confirmação do outro."""
        self._need()
        assert self._ke is not None
        out = b""
        counter = 0
        while len(out) < size:
            out += hmac.new(self._ke, label.encode() + bytes([counter]), hashlib.sha256).digest()
            counter += 1
        return out[:size]
