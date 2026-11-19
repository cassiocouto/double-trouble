"""Autodigita um bloco de texto nesta máquina, usando o programa 'tttttt'.

O texto chega **cifrado** como parâmetro, pelo stdin que o agente abre pro
comando (não por arquivo nem pela área de transferência). Quem cifra é o
controlador, com a senha compartilhada. O comando só repassa o texto cifrado pro
'tttttt' (também pelo stdin), e o 'tttttt' decifra com a mesma senha e digita.

O 'tttttt' é um programa seu, instalado à parte nesta máquina e acessível no
PATH. Ele lê no stdin o texto cifrado (base64, AES-256-CBC + PBKDF2), decifra com
a senha dele e digita na janela em foco.

Dica: use o 'atraso' pra ter tempo de clicar na janela certa antes de começar.
"""

import os
import subprocess
import sys
import time

from relay.command import params, user_path, which

ICONE = "input-keyboard-symbolic"
CATEGORIA = "Teclado"
TIMEOUT = 180
PROGRAMA = "tttttt"

PARAMETROS = [
    {
        "nome": "texto",
        "tipo": "texto",
        "rotulo": "Texto pra digitar",
        "ajuda": "A interface cifra antes de mandar. Pela linha de comando, mande já cifrado.",
        "obrigatorio": True,
        "multilinha": True,
    },
    {
        "nome": "atraso",
        "tipo": "inteiro",
        "rotulo": "Esperar antes de digitar (segundos)",
        "ajuda": "Tempo pra você clicar na janela certa antes de começar.",
        "min": 0,
        "max": 30,
        "padrao": 3,
    },
]

p = params()
texto = p.get("texto", "")
atraso = p.get("atraso", 3)

if not texto.strip():
    sys.exit("não veio texto pra digitar")

# O agente roda como serviço do systemd, com PATH curto. which() procura também
# nas pastas de binário do usuário (~/.local/bin etc.) e no shell de login.
prog = which(PROGRAMA)
if prog is None:
    sys.exit(f"não achei o '{PROGRAMA}' (procurei no PATH, em ~/.local/bin, ~/bin, /usr/local/bin e no shell de login)")

if atraso:
    print(f"digitando em {atraso}s… clique na janela certa", flush=True)
    time.sleep(atraso)

# Passa o PATH completo pro tttttt, caso ele precise de outros programas do usuário.
env = {**os.environ, "PATH": user_path()}
prazo = max(5, TIMEOUT - atraso - 5)
try:
    r = subprocess.run([prog], input=texto, text=True, capture_output=True, timeout=prazo, env=env)
except (OSError, subprocess.TimeoutExpired) as e:
    sys.exit(f"não consegui rodar o {PROGRAMA} ({e})")

if r.returncode != 0:
    detalhe = (r.stderr or r.stdout or "sem detalhe").strip().splitlines()
    sys.exit(f"o {PROGRAMA} falhou: {detalhe[-1] if detalhe else 'código ' + str(r.returncode)}")

print(f"entreguei o texto cifrado pro {PROGRAMA} digitar")
