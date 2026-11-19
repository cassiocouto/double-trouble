"""Chamada à API do Claude (OCR/pergunta) com transporte falso, sem rede."""

from __future__ import annotations

import json
import os

import pytest

from relay.core import vision
from relay.core.errors import ConfigError, RelayError

PNG = b"\x89PNG\r\n\x1a\n" + b"imagem" * 4


def fake_transport(reply_status=200, reply_body=None, capture=None):
    body = reply_body if reply_body is not None else {"content": [{"type": "text", "text": "oi"}]}

    def transport(url, headers, data, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["body"] = json.loads(data)
        raw = reply_body if isinstance(reply_body, bytes) else json.dumps(body).encode()
        return reply_status, raw

    return transport


def settings():
    return vision.VisionSettings(api_key="sk-test", model="claude-opus-5-5", max_tokens=8000)


def test_ocr_builds_image_request_and_returns_text():
    cap = {}
    text = vision.extract_text(PNG, settings=settings(), transport=fake_transport(capture=cap))
    assert text == "oi"
    assert cap["url"] == vision.API_URL
    assert cap["headers"]["x-api-key"] == "sk-test"
    assert cap["headers"]["anthropic-version"] == vision.API_VERSION
    content = cap["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    import base64

    assert base64.b64decode(content[0]["source"]["data"]) == PNG
    assert content[1]["text"] == vision.OCR_PROMPT  # OCR usa o prompt fixo
    assert cap["body"]["model"] == "claude-opus-5-5"


def test_question_mode_uses_the_question():
    cap = {}
    vision.extract_text(
        PNG, mode="pergunta", question="Qual o total?", settings=settings(), transport=fake_transport(capture=cap)
    )
    sent = cap["body"]["messages"][0]["content"][1]["text"]
    assert sent.startswith("Qual o total?")
    assert vision.NO_MARKDOWN in sent  # sempre pede resposta sem markdown
    assert vision.SAME_LANGUAGE in sent  # resposta segue o idioma, não força português
    assert "português do Brasil" not in sent


def test_question_mode_without_question_uses_default_prompt():
    cap = {}
    for q in (None, "", "   "):
        vision.extract_text(
            PNG, mode="pergunta", question=q, settings=settings(), transport=fake_transport(capture=cap)
        )
        sent = cap["body"]["messages"][0]["content"][1]["text"]
        assert sent.startswith(vision.DEFAULT_ASK_PROMPT)
        assert vision.NO_MARKDOWN in sent
        assert vision.SAME_LANGUAGE in sent


def test_strip_markdown_removes_markers():
    raw = "## Título\nIsto é **negrito** e __outro__ e `código`.\n### Sub\ntexto normal"
    out = vision.strip_markdown(raw)
    assert out == "Título\nIsto é negrito e outro e código.\nSub\ntexto normal"


def test_answer_comes_without_markdown():
    body = {"content": [{"type": "text", "text": "A resposta é **42** e `x`"}]}
    out = vision.extract_text(
        PNG, mode="pergunta", question="?", settings=settings(), transport=fake_transport(reply_body=body)
    )
    assert out == "A resposta é 42 e x"


def test_ocr_keeps_markdown_literal():
    # OCR é o texto da tela, não pode mexer: se a tela tem **, fica **
    body = {"content": [{"type": "text", "text": "git log **HEAD**"}]}
    out = vision.extract_text(PNG, mode="ocr", settings=settings(), transport=fake_transport(reply_body=body))
    assert out == "git log **HEAD**"


def test_joins_multiple_text_blocks():
    body = {
        "content": [
            {"type": "text", "text": "linha 1\n"},
            {"type": "thinking", "thinking": "x"},
            {"type": "text", "text": "linha 2"},
        ]
    }
    text = vision.extract_text(PNG, settings=settings(), transport=fake_transport(reply_body=body))
    assert text == "linha 1\nlinha 2"


def test_missing_key_is_a_clear_error():
    with pytest.raises(ConfigError, match="falta a chave"):
        vision.extract_text(PNG, settings=vision.VisionSettings(), transport=fake_transport())


@pytest.mark.parametrize(
    "status, match",
    [
        (401, "recusada"),
        (429, "limitando"),
        (500, "erro"),
        (400, "recusou"),
    ],
)
def test_http_errors(status, match):
    body = json.dumps({"error": {"message": "detalhe"}}).encode()
    with pytest.raises(RelayError, match=match):
        vision.extract_text(PNG, settings=settings(), transport=fake_transport(reply_status=status, reply_body=body))


def test_refusal():
    body = {"stop_reason": "refusal", "content": []}
    with pytest.raises(RelayError, match="recusou"):
        vision.extract_text(PNG, settings=settings(), transport=fake_transport(reply_body=body))


def test_empty_text():
    with pytest.raises(RelayError, match="não devolveu texto"):
        vision.extract_text(PNG, settings=settings(), transport=fake_transport(reply_body={"content": []}))


# --------------------------------------------------------------------------
# configurações da chave


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "claude.json"
    vision.save_settings(vision.VisionSettings(api_key="sk-abc", model="claude-sonnet-5-5", max_tokens=4000), path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    loaded = vision.load_settings(path)
    assert loaded.api_key == "sk-abc" and loaded.model == "claude-sonnet-5-5" and loaded.max_tokens == 4000
    assert loaded.configured


def test_settings_default_when_missing(tmp_path):
    s = vision.load_settings(tmp_path / "nao-existe.json")
    assert not s.configured and s.model == vision.DEFAULT_MODEL


def test_settings_requires_mode_600(tmp_path):
    path = tmp_path / "claude.json"
    vision.save_settings(vision.VisionSettings(api_key="sk"), path)
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        vision.load_settings(path)


def test_bad_max_tokens_falls_back(tmp_path):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"api_key": "sk", "max_tokens": 999999}))
    os.chmod(path, 0o600)
    assert vision.load_settings(path).max_tokens == vision.DEFAULT_MAX_TOKENS


def test_ask_text_sends_text_only_with_the_block():
    cap = {}
    vision.ask_text("qual o total?\n12 + 30", settings=settings(), transport=fake_transport(capture=cap))
    content = cap["body"]["messages"][0]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"  # sem bloco de imagem
    assert "qual o total?" in content[0]["text"]
    assert vision.NO_MARKDOWN in content[0]["text"]
    assert vision.SAME_LANGUAGE in content[0]["text"]


def test_ask_text_empty_block_raises():
    with pytest.raises(RelayError, match="vazio"):
        vision.ask_text("   ", settings=settings(), transport=fake_transport())


def test_ask_text_answer_without_markdown():
    body = {"content": [{"type": "text", "text": "o total e **42**"}]}
    out = vision.ask_text("x", settings=settings(), transport=fake_transport(reply_body=body))
    assert out == "o total e 42"
