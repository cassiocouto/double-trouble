# Decisões

Cada decisão importante, com o porquê em poucas linhas. A mais nova fica no
fim de cada fase.

## Fase 0

**D01. Trabalho feito numa máquina de nuvem, não na cass-dell-brasil.**
O `hostname` aqui é `vm` (Ubuntu 24.04, Python 3.12 do sistema). Por isso
nada aqui toca no `~/.config/ponte` real, e não dá pra acessar a G15. A
instalação nas duas máquinas fica pra você, com os passos no README.

**D02. O `ponte.py` original fica em `legacy/ponte.py`, sem mudanças.**
Os testes da fase 0 carregam esse arquivo direto. Assim dá pra comparar o v1
com o v2 a qualquer momento, e o script antigo continua disponível.

**D03. Bugs do v1 viram testes `xfail(strict=True)`.**
Achei dois. (1) Um `mac` com caractere não-ASCII faz o `hmac.compare_digest`
estourar, o erro cai no `except` do `Handler` e a resposta sai **assinada**
pra quem não tem o segredo. Isso quebra a invariante 3. (2) `ts = NaN` passa
pela checagem de horário, porque `abs(x - nan) > 60` é falso. Só quem tem o
segredo consegue mandar isso, mas é um furo. O `strict=True` avisa se o
comportamento mudar.

**D04. Testes rodam como usuário comum.**
O v2 vai se recusar a rodar como root (invariante 6). Nesta máquina eu sou
root, então rodo a suíte com `setpriv --reuid=65534` (o usuário `nobody`).
Nas suas máquinas, rodar `pytest` como `cass` já basta.

**D05. Venv de desenvolvimento em `.venv` dentro do repositório.**
Criado com o Python do sistema e `--system-site-packages`, igual ao venv que
o instalador vai criar. Fica no `.gitignore`.
