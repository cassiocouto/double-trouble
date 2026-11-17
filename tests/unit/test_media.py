"""Descoberta de tipo por magic bytes."""

from __future__ import annotations

import pytest

from relay.core import media


@pytest.mark.parametrize(
    "data, tipo, imagem, ext",
    [
        (b"\x89PNG\r\n\x1a\n...", "image/png", True, ".png"),
        (b"\xff\xd8\xff\xe0JFIF", "image/jpeg", True, ".jpg"),
        (b"GIF87a...", "image/gif", True, ".gif"),
        (b"GIF89a...", "image/gif", True, ".gif"),
        (b"RIFF\x20\x00\x00\x00WEBPVP8 ", "image/webp", True, ".webp"),
        (b"%PDF-1.7\n...", "application/pdf", False, ".pdf"),
        (b"so um texto\n", "text/plain", False, ".txt"),
        (b"\x00\x01\x02binario\xff", "application/octet-stream", False, ".bin"),
        (b"RIFF\x00\x00\x00\x00AVI \xff", "application/octet-stream", False, ".bin"),  # RIFF mas não WEBP
    ],
)
def test_sniff(data, tipo, imagem, ext):
    kind = media.sniff(data)
    assert kind.tipo == tipo
    assert kind.imagem is imagem
    assert kind.extensao == ext


def test_empty_is_text():
    assert media.sniff(b"") == media.TEXT
