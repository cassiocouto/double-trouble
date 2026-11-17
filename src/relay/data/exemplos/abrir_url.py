"""Abre um link http ou https no navegador desta máquina."""

from urllib.parse import urlparse

from relay.command import open_uri, params

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
# Quem abre é a sessão gráfica desta máquina (o navegador padrão), não o agente.
how = open_uri(url)
notes = {
    "portal": "pelo desktop",
    "systemd": "pelo xdg-open",
    "direto": "direto, sem o systemd (navegador em snap pode não abrir)",
}
print(f"abrindo {url} ({notes[how]})")
