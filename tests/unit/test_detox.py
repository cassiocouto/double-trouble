"""Detoxify: reescreve texto pra soar humano, com transporte falso (sem rede)."""

from __future__ import annotations

import json

import pytest

from relay.core import detox, vision
from relay.core.errors import ConfigError, RelayError


def fake_transport(reply_text="texto reescrito", reply_status=200, capture=None):
    def transport(url, headers, data, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["body"] = json.loads(data)
        body = {"content": [{"type": "text", "text": reply_text}]}
        return reply_status, json.dumps(body).encode()

    return transport


def settings():
    return vision.VisionSettings(api_key="sk-test", model="claude-opus-5-5", max_tokens=8000)


def test_builds_system_request_and_returns_text():
    cap = {}
    out = detox.detoxify_text("texto meio de IA", settings=settings(), transport=fake_transport(capture=cap))
    assert out == "texto reescrito"
    # mesma rota/chave do resto da interface
    assert cap["url"] == vision.API_URL
    assert cap["headers"]["x-api-key"] == "sk-test"
    assert cap["headers"]["anthropic-version"] == vision.API_VERSION
    # o texto do usuário vai como mensagem; as regras vão no system
    assert cap["body"]["model"] == "claude-opus-5-5"
    assert cap["body"]["max_tokens"] == 8000
    assert cap["body"]["messages"][0]["content"][0]["text"] == "texto meio de IA"
    assert detox.BASE_SYSTEM_PROMPT in cap["body"]["system"]


def test_system_prompt_is_base_plus_numbered_rules():
    prompt = detox.build_system_prompt(detox.default_rules())
    assert prompt.startswith(detox.BASE_SYSTEM_PROMPT)
    assert "Apply these transformation rules:" in prompt
    assert "\n1. " in prompt
    for rule in detox.default_rules():
        assert rule.name in prompt
        assert rule.instruction in prompt


def test_default_rules_leave_out_the_structural_ones():
    ids = {r.identifier for r in detox.default_rules()}
    # essas duas remontam parágrafos/linhas e estragariam OCR; ficam desligadas
    assert "merge_paragraphs" not in ids
    assert "reconnect_lines" not in ids
    # mas as que tiram "tells" entram
    assert "em_dash" in ids
    assert "cliches" in ids


def test_rules_can_be_chosen_on_the_call():
    cap = {}
    only = [detox.RULES[0]]
    detox.detoxify_text("oi", settings=settings(), rules=only, transport=fake_transport(capture=cap))
    system = cap["body"]["system"]
    assert only[0].instruction in system
    # uma regra que não foi escolhida não aparece
    outra = next(r for r in detox.RULES if r.identifier == "emoji")
    assert outra.instruction not in system


def test_output_is_stripped():
    out = detox.detoxify_text("oi", settings=settings(), transport=fake_transport(reply_text="  limpo \n"))
    assert out == "limpo"


def test_empty_text_raises():
    with pytest.raises(RelayError):
        detox.detoxify_text("   ", settings=settings(), transport=fake_transport())


def test_no_api_key_raises_configerror():
    with pytest.raises(ConfigError):
        detox.detoxify_text("oi", settings=vision.VisionSettings(), transport=fake_transport())


def test_empty_rule_list_raises():
    with pytest.raises(RelayError):
        detox.build_system_prompt([])


def test_no_stray_em_dash_in_our_ported_text():
    # ao portar, troquei os travessões do detoxify por vírgula/dois-pontos. O
    # texto base e os nomes não têm nenhum; só a regra 'em_dash' cita o caractere
    # pra dizer ao modelo o que tirar.
    assert "—" not in detox.BASE_SYSTEM_PROMPT
    for rule in detox.RULES:
        assert "—" not in rule.name
        if rule.identifier != "em_dash":
            assert "—" not in rule.instruction
