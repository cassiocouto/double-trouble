"""Mostra uma notificação na tela desta máquina."""

import subprocess

from ponte.comando import parametros, quem_chamou

ICONE = "preferences-system-notifications-symbolic"
CATEGORIA = "Tela"
TIMEOUT = 15
PARAMETROS = [
    {"nome": "texto", "tipo": "texto", "rotulo": "Mensagem", "obrigatorio": True, "min": 1, "max": 500},
    {"nome": "urgente", "tipo": "booleano", "rotulo": "Urgente", "ajuda": "Fica na tela até alguém fechar"},
]

p = parametros()
sender = quem_chamou() or "a outra máquina"
cmd = ["notify-send", "--app-name=Ponte"]
if p["urgente"]:
    cmd.append("--urgency=critical")
# O "--" impede que um texto começando com "-" vire opção do notify-send.
subprocess.run([*cmd, "--", f"Mensagem de {sender}", p["texto"]], check=True)
print("notificação mostrada")
