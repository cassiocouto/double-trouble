"""Autodigita um bloco de texto nesta máquina, usando o programa 'autotyper'.

O texto chega como parâmetro, pelo stdin que o agente abre pro comando (não por
arquivo nem pela área de transferência). O comando repassa esse texto pro
'autotyper' também pelo stdin, e o 'autotyper' simula o teclado.

O 'autotyper' é um programa seu, instalado à parte nesta máquina e acessível no
PATH. Ele precisa ler no stdin o texto a digitar e digitar na janela em foco.

Dica: use o 'atraso' pra ter tempo de clicar na janela certa antes de começar.
"""

import shutil
import subprocess
import sys
import time

from relay.command import params

ICONE = "input-keyboard-symbolic"
CATEGORIA = "Teclado"
TIMEOUT = 180
PROGRAMA = "autotyper"

PARAMETROS = [
    {
        "nome": "texto",
        "tipo": "texto",
        "rotulo": "Texto pra digitar",
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

if shutil.which(PROGRAMA) is None:
    sys.exit(f"o '{PROGRAMA}' não está instalado nesta máquina (precisa estar no PATH)")

if atraso:
    print(f"digitando em {atraso}s… clique na janela certa", flush=True)
    time.sleep(atraso)

prazo = max(5, TIMEOUT - atraso - 5)
try:
    r = subprocess.run([PROGRAMA], input=texto, text=True, capture_output=True, timeout=prazo)
except (OSError, subprocess.TimeoutExpired) as e:
    sys.exit(f"não consegui rodar o {PROGRAMA} ({e})")

if r.returncode != 0:
    detalhe = (r.stderr or r.stdout or "sem detalhe").strip().splitlines()
    sys.exit(f"o {PROGRAMA} falhou: {detalhe[-1] if detalhe else 'código ' + str(r.returncode)}")

print(f"digitei {len(texto)} caractere(s) com o {PROGRAMA}")
