# Ponte

Uma máquina roda comandos pré-aprovados na outra, pelo Tailscale.

- **Agente**: o lado que executa. Fica rodando como serviço do seu usuário.
- **Controlador**: o lado que manda. É a interface (o app **Ponte**) ou a
  linha de comando (`ponte`).

A mesma máquina pode ter os dois. Feito pra `cass-dell-brasil` e
`cass-dell-g15-5511`, mas funciona com qualquer número de máquinas.

O agente só roda arquivos que **você** colocou em `~/.config/ponte/comandos/`.
Não existe campo de comando livre, nem shell.

---

## Instalar

Faça isto **nas duas máquinas**.

**1. Pacotes do sistema.** Só uma vez:

```bash
sudo apt install python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin
```

O instalador confere isso e mostra a linha certa se faltar algo.

**2. Baixe o projeto e rode o instalador.** Sem sudo:

```bash
git clone https://github.com/cassiocouto/double-trouble.git ~/src/ponte
```

```bash
~/src/ponte/scripts/install.sh
```

Ele cria um venv em `~/.local/share/ponte/venv`, instala o agente como
serviço do usuário, coloca o Ponte no menu de aplicativos e cria os comandos
de exemplo. Se achar o `ponte.py` antigo, ele mostra o plano de migração e
pergunta antes (veja "Vindo do ponte.py v1").

Opções: `--agente` (só o lado que executa), `--gui` (só a interface).

**3. Firewall.** Se o `ufw` estiver ligado, o instalador mostra estas duas
linhas. Elas liberam o ponte só na interface do Tailscale:

```bash
sudo ufw allow in on tailscale0 to any port 47800 proto tcp comment 'ponte'
```

```bash
sudo ufw allow in on tailscale0 to any port 47801 proto tcp comment 'ponte pareamento'
```

**4. Parear.** Uma vez pra cada direção. Exemplo: a brasil manda na G15.

Na **G15** (a que vai executar):

```bash
ponte parear
```

Ela mostra um código de 8 números, válido por 2 minutos.

Na **brasil** (a que vai mandar):

```bash
ponte parear --com cass-dell-g15-5511
```

Digite o código quando ele pedir (o código nunca vai na linha de comando).
Pronto. Teste:

```bash
ponte ping g15
```

Pra G15 mandar na brasil também, faça o mesmo trocando os papéis.

Pela interface é igual: **Configurações → Esta máquina → Deixar outra
controlar** mostra o código, e **Configurações → Máquinas → Parear com
código** recebe ele.

---

## Usar

**Pela interface.** Abra o **Ponte** no menu de aplicativos.

1. A tela inicial mostra as máquinas, se estão online, a latência, a
   diferença de relógio e a versão do agente.
2. Clique numa máquina pra ver os comandos dela, com busca.
3. Clique num comando. Preencha o formulário (se tiver) e clique em
   **Rodar**. A saída aparece ao vivo. Dá pra **Cancelar** e **Copiar saída**.
4. O **Histórico** (o relógio no topo) guarda o que você rodou, com
   **Rodar de novo**.

Se um job demorar mais de 10 s e a janela não estiver em foco, chega uma
notificação quando ele acabar.

**Pela linha de comando.** O nome da máquina pode ser só um pedaço (`g15`,
`brasil`).

```bash
ponte ping g15
```

```bash
ponte listar g15
```

```bash
ponte rodar g15 status
```

```bash
ponte rodar g15 notificar "oi do outro lado"
```

```bash
ponte rodar g15 notificar -p texto="oi" -p urgente=sim
```

```bash
ponte historico g15
```

O `rodar` mostra a saída ao vivo. O primeiro **Ctrl+C** cancela o job lá na
outra máquina. O segundo sai na hora.

Pra jobs longos: `ponte rodar g15 backup --sem-esperar` mostra o id do job.
Depois, `ponte status g15 <id> --seguir` acompanha e `ponte cancelar g15 <id>`
para.

Outros comandos úteis: `ponte maquinas`, `ponte comandos`, `ponte config`,
`ponte agente revogar <nome>`. Veja todos com `ponte --help`.

---

## Criar um comando novo

Um comando é um arquivo Python em `~/.config/ponte/comandos/`. O nome do
arquivo vira o nome do comando: letras minúsculas, números, `_` e `-`.

Exemplo, `~/.config/ponte/comandos/volume.py`:

```python
"""Muda o volume do som.

Usa o wpctl do PipeWire.
"""

import subprocess

from ponte.comando import parametros

ICONE = "audio-volume-high-symbolic"
CATEGORIA = "Som"
TIMEOUT = 10
PARAMETROS = [
    {"nome": "nivel", "tipo": "inteiro", "rotulo": "Volume (%)", "min": 0, "max": 100, "padrao": 50},
    {"nome": "mudo", "tipo": "booleano", "rotulo": "Sem som"},
]

p = parametros()
subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{p['nivel']}%"], check=True)
subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if p["mudo"] else "0"], check=True)
print(f"volume em {p['nivel']}%")
```

Depois, confira se ele foi aceito:

```bash
ponte comandos
```

Se aparecer **BLOQUEADO**, a linha diz o motivo.

**Permissões.** O arquivo precisa ser seu e não pode ter escrita pra grupo
nem pra outros. Na dúvida:

```bash
chmod 600 ~/.config/ponte/comandos/volume.py
```

### O que dá pra colocar no arquivo

Tudo é opcional. O agente lê isso **sem executar o arquivo**, então precisa
ser um valor escrito direto (nada de conta ou função).

| Nome | O que faz | Padrão |
|---|---|---|
| docstring | A primeira linha é a descrição. O resto aparece como detalhe. | vazio |
| `PARAMETROS` | Lista de parâmetros (veja abaixo). A interface monta o formulário. | nenhum |
| `CONFIRMAR = True` | Pergunta "tem certeza?" antes de rodar. | `False` |
| `TIMEOUT = 30` | Limite em segundos (até 3600). Passou, o comando morre. | 60 |
| `ICONE = "nome-symbolic"` | Ícone do tema, mostrado na interface. | um ícone genérico |
| `CATEGORIA = "Som"` | Agrupa os comandos na interface. | "Outros" |
| `ACEITA_ARGS = True` | Jeito antigo: aceita argumentos soltos no `sys.argv`. | `False` |

### Parâmetros

Cada item de `PARAMETROS` é um dicionário com `nome` e `tipo`, e mais o que
precisar:

| Chave | Pra quê | Tipos |
|---|---|---|
| `nome` | Nome do campo (minúsculo, começa com letra) | todos |
| `tipo` | `texto`, `inteiro`, `booleano` ou `escolha` | todos |
| `rotulo` | Texto do campo na tela | todos |
| `ajuda` | Uma frase de ajuda | todos |
| `obrigatorio` | `True` se não pode ficar vazio | todos |
| `padrao` | Valor inicial | todos |
| `min`, `max` | Inteiro: o valor. Texto: o tamanho. | texto, inteiro |
| `regex` | O texto inteiro precisa bater com ela. Use regex simples. | texto |
| `multilinha` | Caixa de várias linhas | texto |
| `segredo` | Campo de senha. Não vai pro log nem pro histórico. | texto |
| `opcoes` | Lista de textos pra escolher | escolha |

O agente confere tudo antes de rodar. Os valores chegam como JSON no stdin
do comando, e `parametros()` lê isso pra você. Um parâmetro opcional sem
valor chega como `None` (ou `False`, se for booleano).

### Dicas

- `from ponte.comando import quem_chamou` diz qual máquina pediu.
- O que o comando imprimir volta pra quem chamou. O código de saída também.
- **Programa que precisa continuar aberto** (um navegador, um app): use
  `soltar(["xdg-open", url])` de `ponte.comando`. Sem isso, ele morre quando o
  comando acabar, porque o agente encerra tudo que o comando deixou rodando.
- `sudo` e `pkexec` não funcionam dentro de um comando, de propósito.
- Nunca monte uma linha de shell com os parâmetros. Passe uma lista pro
  `subprocess.run`, como no exemplo.

Os quatro exemplos (`status`, `notificar`, `travar_tela`, `abrir_url`) estão
na pasta de comandos. Pra trocar versões antigas pelas novas:
`ponte exemplos --atualizar` (pergunta antes e guarda as antigas).

---

## Quando algo não funciona

**Ver os logs do agente.** Quase sempre a resposta está aqui:

```bash
journalctl --user -u ponte-agent -f
```

E o estado do serviço:

```bash
systemctl --user status ponte-agent
```

**"esperando o Tailscale subir".** O agente escuta no IP do Tailscale. Ele
espera sozinho, mas confira:

```bash
tailscale status
```

Se precisar, `sudo tailscale up`.

**"não consegui falar com ..." ou "não respondeu".** A outra máquina está
desligada, fora do Tailscale, ou o agente lá está parado. Na outra máquina,
veja os logs (acima). Se o `ufw` estiver ligado, libere as portas (veja
"Instalar", passo 3).

**"fechou a conexão sem responder".** O IP de quem chamou está fora da lista
`permitidos` do agente, o config do agente está com problema (veja os logs
lá), ou lá ainda está rodando o `ponte.py` antigo.

**"recusou sem assinar" / "não aceitou esta máquina".** As duas máquinas não
estão pareadas nessa direção (ou foram revogadas). Pareie de novo com
`ponte parear`.

**"relógios fora de sincronia".** A diferença passou de 60 s. Confira nas duas:

```bash
timedatectl
```

Se "System clock synchronized" estiver "no":

```bash
sudo timedatectl set-ntp true
```

**O comando aparece como BLOQUEADO.** Rode `ponte comandos` na máquina do
comando. O motivo aparece do lado: permissão aberta (`chmod 600 arquivo`),
erro de sintaxe, `PARAMETROS` inválido, link simbólico.

**"tem permissões abertas demais" no config.** Rode `chmod 600
~/.config/ponte/ponte.json`. Com o config inseguro, o agente recusa tudo até
você consertar. É de propósito.

**A interface não abre.** Rode `ponte-gui` num terminal e veja a mensagem.
Normalmente falta pacote do apt (veja "Instalar", passo 1).

**Um navegador aberto pelo comando fecha sozinho.** Use `soltar()` (veja
"Dicas").

**Auditoria.** Tudo que o agente recusou ou rodou fica em
`~/.local/state/ponte/auditoria.jsonl` (quem, o quê, parâmetros, código,
duração).

---

## Vindo do ponte.py v1

O instalador detecta o `ponte.service` antigo e pergunta. Ou rode:

```bash
ponte migrar
```

Ele mostra o plano e pergunta antes. O que acontece:

- o `ponte.service` antigo é parado e desabilitado (o arquivo fica);
- o `config.json` do v1 **não muda**, e ganha um backup do lado;
- o v2 grava `~/.config/ponte/ponte.json` com o mesmo nome, porta, máquinas e
  segredo;
- a pasta de comandos não muda. Os comandos antigos continuam funcionando.

**Depois de migrar, pareie de novo** nas duas direções. O v1 usava o mesmo
segredo pras duas, e o v2 prefere um por direção.

O v1 e o v2 não conversam entre si. Atualize as duas máquinas.

Pra voltar pro v1:

```bash
systemctl --user disable --now ponte-agent
```

```bash
systemctl --user enable --now ponte.service
```

---

## Desinstalar

```bash
~/src/ponte/scripts/uninstall.sh
```

Isso tira o serviço, o venv, o atalho e o ícone. Config, comandos, auditoria
e histórico ficam. Pra apagar tudo, use `--tudo` (ele pergunta antes).

---

## Mais detalhes

- `docs/SEGURANCA.md`: do que o ponte se protege e como.
- `docs/PROTOCOLO.md`: o protocolo v2, mensagem por mensagem.
- `DECISIONS.md`: cada decisão importante, com o porquê.

## Desenvolvimento

```bash
python3 -m venv --system-site-packages .venv
```

```bash
.venv/bin/pip install pytest ruff
```

```bash
.venv/bin/pytest
```

```bash
.venv/bin/ruff check src tests
```

Os testes nunca usam o seu `~/.config/ponte` e nunca rodam comandos que mexem
na tela. Eles precisam rodar como usuário comum (o agente se recusa a rodar
como root). Se tiver GTK 4 e `xvfb-run`, um teste abre a interface numa tela
falsa.
