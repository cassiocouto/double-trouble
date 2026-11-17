"""Descobre o tipo de um arquivo pelos primeiros bytes (magic bytes).

O agente não confia no que o comando diz que escreveu: ele mesmo olha os
primeiros bytes. Assim um comando não marca um binário qualquer como imagem.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024  # um print cabe de sobra; acima disso o agente recusa
ARTIFACT_CHUNK = 512 * 1024  # bytes por frame (em base64 dá ~683 KiB, abaixo de 1 MiB)


@dataclass(frozen=True)
class MediaKind:
    tipo: str  # media type, ex. "image/png"
    extensao: str  # com o ponto, ex. ".png"
    imagem: bool = False


OCTET = MediaKind("application/octet-stream", ".bin")
TEXT = MediaKind("text/plain", ".txt")

_SIGNATURES: tuple[tuple[bytes, MediaKind], ...] = (
    (b"\x89PNG\r\n\x1a\n", MediaKind("image/png", ".png", imagem=True)),
    (b"\xff\xd8\xff", MediaKind("image/jpeg", ".jpg", imagem=True)),
    (b"GIF87a", MediaKind("image/gif", ".gif", imagem=True)),
    (b"GIF89a", MediaKind("image/gif", ".gif", imagem=True)),
    (b"%PDF-", MediaKind("application/pdf", ".pdf")),
)


def sniff(data: bytes) -> MediaKind:
    for prefix, kind in _SIGNATURES:
        if data.startswith(prefix):
            return kind
    # WebP é "RIFF" + 4 bytes de tamanho + "WEBP".
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MediaKind("image/webp", ".webp", imagem=True)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return OCTET
    return TEXT
