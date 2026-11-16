"""Abre um link http ou https no navegador desta máquina."""

from urllib.parse import urlparse

from relay.command import detach, params

ICONE = "web-browser-symbolic"
CATEGORIA = "Tela"
TIMEOUT = 15
PARAMETROS = [
    {
        "nome": "url",
        "tipo": "texto",
        "rotulo": "Link",
        "obrigatorio": True,
        "max": 2000,
        "regex": r"https?://[^\s]+",
        "ajuda": "Começa com http:// ou https://",
    },
]

url = params()["url"]
parts = urlparse(url)
if parts.scheme not in ("http", "https") or not parts.netloc:
    raise SystemExit("só aceito links http ou https")
# O navegador precisa sair do grupo do comando, senão morre quando o comando acabar.
detach(["xdg-open", url])
print(f"abrindo {url}")
