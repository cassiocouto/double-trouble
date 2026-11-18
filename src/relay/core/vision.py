"""Manda uma imagem pra API do Claude e recebe o texto (OCR) ou a resposta.

Roda no controlador (quem usa a interface). A chave da API fica só nesta
máquina, num arquivo com permissão 600. Em tempo de execução o projeto só usa
a biblioteca padrão, então a chamada vai por HTTPS direto com urllib, sem o
SDK. O transporte é injetável, pra os testes não tocarem na rede.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import ConfigError, RelayError
from .safety import read_private_text, write_private
from .strictjson import loads_strict

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 8000
MAX_TOKENS_LIMIT = 64000
TIMEOUT = 120

# Pedido de OCR. Pede só o texto, na ordem em que aparece, sem comentários.
OCR_PROMPT = (
    "Extraia todo o texto desta imagem exatamente como aparece, na mesma ordem "
    "e mantendo as quebras de linha. Responda apenas com o texto, sem nenhum "
    "comentário seu, sem aspas e sem blocos de código."
)

# Usado no modo "pergunta" quando você não digita nada: o modelo faz o melhor
# que dá com o que está no recorte.
DEFAULT_ASK_PROMPT = (
    "Olhe este recorte da tela e me ajude da melhor forma possível. Se houver "
    "uma pergunta ou um exercício, responda. Se houver código, explique o que "
    "ele faz, mostre os problemas ou complete o que falta. Senão, diga o que é "
    "importante aqui e o que fazer a seguir. Responda em português do Brasil."
)

# Grudado no fim da pergunta: o bloco de texto vai pra OCR, autodigitação etc.,
# então a resposta tem que ser texto puro, sem markdown.
NO_MARKDOWN = (
    "Responda em texto puro, sem formatação markdown: nada de ** ou __ para "
    "negrito, nada de # para título, nada de crases para código e nada de "
    "listas com marcador."
)


def strip_markdown(text: str) -> str:
    """Tira os enfeites de markdown (negrito, código, títulos), mantendo o texto.

    Serve pra resposta do modo "pergunta", não pro OCR (que é o texto literal
    da tela e não deve ser mexido).
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **negrito**
    text = re.sub(r"__(.+?)__", r"\1", text)  # __negrito__
    text = re.sub(r"`+([^`]+?)`+", r"\1", text)  # `código`
    lines = [re.sub(r"^\s{0,3}#{1,6}\s+", "", line) for line in text.split("\n")]  # ## título
    return "\n".join(lines)


@dataclass
class VisionSettings:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def to_json(self) -> dict:
        return {"api_key": self.api_key, "model": self.model, "max_tokens": self.max_tokens}

    @classmethod
    def from_json(cls, data: object) -> VisionSettings:
        if not isinstance(data, dict):
            raise ConfigError("o config da API do Claude precisa ser um objeto JSON")
        key = data.get("api_key", "")
        model = data.get("model") or DEFAULT_MODEL
        mt = data.get("max_tokens", DEFAULT_MAX_TOKENS)
        if not isinstance(key, str):
            raise ConfigError("a chave da API precisa ser texto")
        if not isinstance(model, str) or not model:
            raise ConfigError("o modelo precisa ser texto")
        if not isinstance(mt, int) or isinstance(mt, bool) or not 0 < mt <= MAX_TOKENS_LIMIT:
            mt = DEFAULT_MAX_TOKENS
        return cls(api_key=key.strip(), model=model.strip(), max_tokens=mt)


def settings_path() -> Path:
    return paths.base_dir() / "claude.json"


def load_settings(path: Path | None = None) -> VisionSettings:
    path = path or settings_path()
    if not path.exists():
        return VisionSettings()
    text = read_private_text(path, "o config da API do Claude")
    try:
        return VisionSettings.from_json(loads_strict(text))
    except ValueError as e:
        raise ConfigError(f"{path} não é JSON válido ({e})") from None


def save_settings(settings: VisionSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    paths.ensure_private_dir(path.parent)
    write_private(path, json.dumps(settings.to_json(), indent=2) + "\n", mode=0o600)


# --------------------------------------------------------------------------
# a chamada


Transport = Callable[[str, dict, bytes, float], tuple[int, bytes]]


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 (https fixo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise RelayError(f"não consegui falar com a API do Claude ({e.reason})") from None
    except (TimeoutError, OSError) as e:
        raise RelayError(f"não consegui falar com a API do Claude ({e})") from None


def build_request(settings: VisionSettings, image_png: bytes, prompt: str) -> dict:
    return {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image_png).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }


def _message_text(data: dict) -> str:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise RelayError("a API do Claude respondeu num formato inesperado")
    parts = [
        b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    return "".join(parts).strip()


def _error_message(status: int, body: bytes) -> str:
    detail = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            detail = str(data["error"].get("message", ""))[:300]
    except ValueError:
        pass
    if status == 401:
        return "a chave da API do Claude foi recusada (401). Confira a chave nas Configurações"
    if status == 429:
        return "a API do Claude está limitando as chamadas (429). Espere um pouco e tente de novo"
    if status >= 500:
        return f"a API do Claude teve um erro ({status}). Tente de novo daqui a pouco"
    return f"a API do Claude recusou ({status}{': ' + detail if detail else ''})"


def extract_text(
    image_png: bytes,
    *,
    mode: str = "ocr",
    question: str | None = None,
    settings: VisionSettings,
    transport: Transport = _post,
) -> str:
    """Manda a imagem e devolve o texto. mode 'ocr' lê o texto; 'pergunta' responde `question`."""
    if not settings.configured:
        raise ConfigError("falta a chave da API do Claude. Ponha ela nas Configurações")
    if mode == "pergunta":
        # Sem texto, o modelo faz o melhor que dá com o recorte. Em todo caso,
        # pede resposta sem markdown.
        base = (question or "").strip() or DEFAULT_ASK_PROMPT
        prompt = f"{base}\n\n{NO_MARKDOWN}"
    else:
        prompt = OCR_PROMPT
    body = json.dumps(build_request(settings, image_png, prompt)).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "anthropic-version": API_VERSION,
        "x-api-key": settings.api_key,
    }
    status, raw = transport(API_URL, headers, body, TIMEOUT)
    if status != 200:
        raise RelayError(_error_message(status, raw))
    try:
        data = json.loads(raw)
    except ValueError:
        raise RelayError("a API do Claude respondeu algo que não é JSON") from None
    if not isinstance(data, dict):
        raise RelayError("a API do Claude respondeu num formato inesperado")
    if data.get("stop_reason") == "refusal":
        raise RelayError("o Claude recusou responder sobre esta imagem")
    text = _message_text(data)
    if mode == "pergunta":
        text = strip_markdown(text)  # OCR fica literal; resposta vem sem markdown
    if not text:
        raise RelayError("o Claude não devolveu texto nenhum pra esta imagem")
    return text
