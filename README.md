# Relay

Uma máquina roda comandos pré-aprovados na outra, pelo Tailscale.

- **Agente**: o lado que executa. Fica rodando como serviço do seu usuário.
- **Controlador**: o lado que manda. É a interface (o app **Relay**) ou a
  linha de comando (`relay`).

A mesma máquina pode ter os dois. Funciona com qualquer número de máquinas.

O agente só roda arquivos que **você** colocou em `~/.config/relay/comandos/`.
Não existe campo de comando livre, nem shell.

## As suas duas máquinas

| Máquina | Papel agora | Faz o quê |
|---|---|---|
| `cass-hp-elitedesk-800-g6` | **thinker** | Pensa e manda comandos pro gêmeo |
| `cass-dell-g15-5511` | **gêmeo** | Executa o que o thinker manda e manda informação pro thinker |

Os papéis podem trocar. As duas máquinas recebem o Relay inteiro (agente e
interface), e um pareamento só já libera **as duas direções**. Trocar de papel
não pede nenhuma mudança. É só mandar pela outra máquina.

Como o gêmeo manda informação pro thinker?

- Quando o thinker roda um comando no gêmeo, a saída volta ao vivo pro
  thinker. Essa é a forma mais comum.
- O gêmeo também pode tomar a iniciativa e rodar um comando no thinker, por
  exemplo `relay rodar hp notificar "terminei o backup"`.

O nome pode ser só um pedaço. `hp` acha a `cass-hp-elitedesk-800-g6`, e `g15`
acha a `cass-dell-g15-5511`.

---

## Instalar

Faça os passos 1 a 3 **nas duas máquinas**.

**1. Pacotes do sistema.** Só uma vez:

```bash
sudo apt install python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin
```

O instalador confere isso e mostra a linha certa se faltar algo.

**2. Baixe o projeto e rode o instalador.** Sem sudo:

```bash
git clone -b main https://github.com/cassiocouto/double-trouble.git ~/src/relay
```

```bash
~/src/relay/scripts/install.sh
```

Ele cria um venv em `~/.local/share/relay/venv`, instala o agente como
serviço do usuário, coloca o Relay no menu de aplicativos e cria os comandos
de exemplo.

Opções: `--agente` (só o lado que executa), `--gui` (só a interface).

**3. Firewall.** Se o `ufw` estiver ligado, o instalador mostra estas duas
linhas. Elas liberam o relay só na interface do Tailscale:

```bash
sudo ufw allow in on tailscale0 to any port 47800 proto tcp comment 'relay'
```

```bash
sudo ufw allow in on tailscale0 to any port 47801 proto tcp comment 'relay pareamento'
```

**4. Parear as duas.** Uma vez só. Depois disso as duas mandam uma na outra.

Numa máquina, por exemplo a **G15**:

```bash
relay parear
```

Ela mostra um código de 8 números, válido por 2 minutos.

Na outra, a **HP**:

```bash
relay parear g15
```

Digite o código quando ele pedir. Dá pra usar só um pedaço do nome (`g15`,
`hp`). O Relay acha a máquina no Tailscale.

**5. Testar.** Rode `relay` sozinho, nas duas. Ele mostra com quem a máquina
está pareada, se a outra está online, e os comandos mais usados.

```bash
relay
```

**6. Opcional: limitar o que cada uma pode rodar na outra.** Por padrão uma
máquina pareada pode rodar todos os comandos da outra. Pra deixar a G15 rodar
só `notificar` e `status` na HP, rode na **HP**:

```bash
relay agente permissoes cass-dell-g15-5511 notificar status
```

Pra voltar a liberar tudo, use `*` no lugar da lista. Sem lista, ele só mostra
o que está liberado. Na interface: **Configurações → Esta máquina**, no botão
de engrenagem do lado da máquina.

Pela interface o pareamento é igual. Em **Configurações**, uma máquina usa
**Mostrar código** e a outra usa **Digitar código**.

---

## Usar

**Pela interface.** Abra o **Relay** no menu de aplicativos.

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
`hp`).

```bash
relay ping g15
```

```bash
relay listar g15
```

```bash
relay rodar g15 status
```

```bash
relay rodar g15 notificar "oi do outro lado"
```

```bash
relay rodar g15 notificar -p texto="oi" -p urgente=sim
```

```bash
relay historico g15
```

O `rodar` mostra a saída ao vivo. O primeiro **Ctrl+C** cancela o job lá na
outra máquina. O segundo sai na hora.

Pra jobs longos: `relay rodar g15 backup --sem-esperar` mostra o id do job.
Depois, `relay status g15 <id> --seguir` acompanha e `relay cancelar g15 <id>`
para.

Outros comandos úteis: `relay maquinas`, `relay comandos`, `relay config`,
`relay agente revogar <nome>`. Veja todos com `relay --help`.

### Ler o texto da tela (OCR)

Dá pra tirar um print da outra máquina, recortar um pedaço e deixar o Claude
ler o texto, na interface. Serve pra juntar o texto de várias telas aos poucos.

1. Uma vez, ponha sua chave da API do Claude em **Configurações → Claude (OCR)**.
   Ela fica só nesta máquina, num arquivo com permissão restrita.
2. Abra a máquina, clique no ícone de print no topo (**Ler o texto da tela**).
3. **Tirar print**, arraste um retângulo sobre o texto, **Extrair recorte**. O
   texto aparece no bloco embaixo.
4. Repita quantas vezes quiser. Cada recorte é concatenado no mesmo bloco.
5. **Liberar** copia o bloco e deixa você salvar num arquivo, e começa um bloco
   novo.

Em vez de "Ler o texto", dá pra escolher **Perguntar ao Claude** e digitar uma
pergunta sobre o recorte. O modelo padrão é o `claude-opus-5-5`; dá pra trocar
por um mais barato nas Configurações. Cada leitura é uma chamada paga à API.

---

## Criar um comando novo

Um comando é um arquivo Python em `~/.config/relay/comandos/`. O nome do
arquivo vira o nome do comando: letras minúsculas, números, `_` e `-`.

Exemplo, `~/.config/relay/comandos/volume.py`:

```python
"""Muda o volume do som.

Usa o wpctl do PipeWire.
"""

import subprocess

from relay.command import params

ICONE = "audio-volume-high-symbolic"
CATEGORIA = "Som"
TIMEOUT = 10
PARAMETROS = [
    {"nome": "nivel", "tipo": "inteiro", "rotulo": "Volume (%)", "min": 0, "max": 100, "padrao": 50},
    {"nome": "mudo", "tipo": "booleano", "rotulo": "Sem som"},
]

p = params()
subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{p['nivel']}%"], check=True)
subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if p["mudo"] else "0"], check=True)
print(f"volume em {p['nivel']}%")
```

Depois, confira se ele foi aceito:

```bash
relay comandos
```

Se aparecer **BLOQUEADO**, a linha diz o motivo.

**Permissões.** O arquivo precisa ser seu e não pode ter escrita pra grupo
nem pra outros. Na dúvida:

```bash
chmod 600 ~/.config/relay/comandos/volume.py
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
do comando, e `params()` lê isso pra você. Um parâmetro opcional sem
valor chega como `None` (ou `False`, se for booleano).

### Dicas

- `from relay.command import caller` diz qual máquina pediu.
- O que o comando imprimir volta pra quem chamou. O código de saída também.
- **Devolver uma imagem ou arquivo** (um print, um PDF): escreva os bytes no
  caminho que está em `os.environ["RELAY_OUTPUT_FILE"]`. O arquivo volta pra
  quem chamou. Na interface, uma imagem aparece na hora; pela linha de comando,
  use `relay rodar <maq> <cmd> --salvar <pasta>`. O arquivo pode ter até 8 MiB.
- **Abrir um link ou arquivo** na tela da máquina: use `open_uri(url)` de
  `relay.command`. Quem abre é o próprio desktop, com o programa padrão.
  Funciona com o navegador que já estava aberto e com navegador em snap.
- **Abrir um programa que precisa continuar aberto** (um app): use
  `detach(["gnome-calculator"])`. Sem isso, ele morre quando o comando acabar,
  porque o agente encerra tudo que o comando deixou rodando.
- `sudo` e `pkexec` não funcionam dentro de um comando, de propósito.
- Nunca monte uma linha de shell com os parâmetros. Passe uma lista pro
  `subprocess.run`, como no exemplo.

Os exemplos (`status`, `notificar`, `travar_tela`, `abrir_url`, `screenshot`)
estão na pasta de comandos. O `screenshot` tira um print da tela, sem
notificação nem atalho de teclado, e devolve a imagem. O `install.sh` traz os
exemplos novos sozinho a cada atualização, sem tocar nos que você já tem nem no
config (então rodar `git pull && ./scripts/install.sh` já basta). Pra trocar
os exemplos antigos pelas versões novas: `relay exemplos --atualizar` (pergunta
antes e guarda as antigas como `.bak`).

---

## Quando algo não funciona

**Ver os logs do agente.** Quase sempre a resposta está aqui:

```bash
journalctl --user -u relay-agent -f
```

E o estado do serviço:

```bash
systemctl --user status relay-agent
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
lá), ou lá ainda está rodando uma versão antiga.

**"recusou sem assinar" / "não aceitou esta máquina".** As duas máquinas não
estão pareadas nessa direção (ou foram revogadas). Pareie de novo com
`relay parear`.

**"relógios fora de sincronia".** A diferença passou de 60 s. Confira nas duas:

```bash
timedatectl
```

Se "System clock synchronized" estiver "no":

```bash
sudo timedatectl set-ntp true
```

**O comando aparece como BLOQUEADO.** Rode `relay comandos` na máquina do
comando. O motivo aparece do lado: permissão aberta (`chmod 600 arquivo`),
erro de sintaxe, `PARAMETROS` inválido, link simbólico.

**"tem permissões abertas demais" no config.** Rode `chmod 600
~/.config/relay/relay.json`. Com o config inseguro, o agente recusa tudo até
você consertar. É de propósito.

**A interface não abre.** Rode `relay-gui` num terminal e veja a mensagem.
Normalmente falta pacote do apt (veja "Instalar", passo 1).

**O comando diz que abriu, mas nada aparece na tela.** O link ou programa
abre na máquina **onde o comando roda**, não na que mandou. Se for lá mesmo,
use `open_uri()` pra links e `detach()` pra programas (veja "Dicas"). Se o seu
`abrir_url` é de antes dessa correção, troque pela versão nova com
`relay exemplos --atualizar`.

**Auditoria.** Tudo que o agente recusou ou rodou fica em
`~/.local/state/relay/auditoria.jsonl` (quem, o quê, parâmetros, código,
duração).

---

## Desinstalar

```bash
~/src/relay/scripts/uninstall.sh
```

Isso tira o serviço, o venv, o atalho e o ícone. Config, comandos, auditoria
e histórico ficam. Pra apagar tudo, use `--tudo` (ele pergunta antes).

---

## Mais detalhes

- `docs/SEGURANCA.md`: do que o relay se protege e como.
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

Os testes nunca usam o seu `~/.config/relay` e nunca rodam comandos que mexem
na tela. Eles precisam rodar como usuário comum (o agente se recusa a rodar
como root). Se tiver GTK 4 e `xvfb-run`, um teste abre a interface numa tela
falsa.
