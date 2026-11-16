"""Mostra uma notificação na tela desta máquina."""

import subprocess

from relay.command import caller, params

ICONE = "preferences-system-notifications-symbolic"
CATEGORIA = "Tela"
TIMEOUT = 15
PARAMETROS = [
    {"nome": "texto", "tipo": "texto", "rotulo": "Mensagem", "obrigatorio": True, "min": 1, "max": 500},
    {"nome": "urgente", "tipo": "booleano", "rotulo": "Urgente", "ajuda": "Fica na tela até alguém fechar"},
]

p = params()
sender = caller() or "a outra máquina"
cmd = ["notify-send", "--app-name=Relay"]
if p["urgente"]:
    cmd.append("--urgency=critical")
# O "--" impede que um texto começando com "-" vire opção do notify-send.
subprocess.run([*cmd, "--", f"Mensagem de {sender}", p["texto"]], check=True)
print("notificação mostrada")
