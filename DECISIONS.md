# Decisões

Cada decisão importante, com o porquê em poucas linhas. A mais nova fica no
fim de cada fase.

## Fase 0

**D01. Trabalho feito numa máquina de nuvem, não na cass-dell-brasil.**
O `hostname` aqui é `vm` (Ubuntu 24.04, Python 3.12 do sistema). Por isso
nada aqui toca no `~/.config/relay` real, e não dá pra acessar a G15. A
instalação nas duas máquinas fica pra você, com os passos no README.

**D02. O script original ficou no repositório durante a reescrita.**
Os testes da fase 0 carregavam esse arquivo direto, pra comparar o v1 com o v2.
Saiu depois, junto com a migração (ver D70).

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

## Fase 1

**D06. Layout `src/` com subpacotes `core`, `agent`, `cli` e `gui`.**
O layout `src/` evita que os testes importem o código da pasta errada. O
`core` não importa nada de `agent`, `cli` ou `gui`.

**D07. Na fase 1 o pacote fala o protocolo v1.**
Assim os 59 testes da fase 0 rodam contra o script antigo e contra o pacote,
e um teste de interoperabilidade mostra cliente novo com servidor velho e o
contrário. Isso prova que a reorganização não mudou o comportamento.

**D08. O comando roda pelo fd que foi conferido.**
O agente abre o arquivo com `O_NOFOLLOW` (e `O_NONBLOCK`, pra um FIFO não
travar tudo), confere dono e modo pelo `fstat`, lê os metadados desse
conteúdo e executa `/proc/self/fd/N`. Trocar o arquivo entre a checagem e a
execução não muda o que roda. O `__file__` do comando vira `/proc/self/fd/N`,
então o caminho real vai em `RELAY_COMMAND_FILE`.

**D09. O grupo de processos morre quando o comando acaba.**
No v1, um filho deixado pelo comando vivia pra sempre, sem timeout. Agora,
quando o processo principal termina, o resto do grupo recebe SIGTERM e,
3 s depois, SIGKILL. O líder só é colhido depois disso (`waitid` com
`WNOWAIT`), então o número do grupo nunca é reusado no meio do caminho. Quem
quer deixar um programa aberto (um navegador) solta ele de propósito com
`relay.command.detach()`. Isso fecha a invariante 9 sem exceção.

**D10. Saída lida por pipe, com limite, e o excesso é descartado.**
O v1 mandava a saída pra arquivo temporário, que podia crescer sem limite no
disco. Agora o agente lê o pipe sem bloquear, guarda até 256 KiB por fluxo e
joga o resto fora, sem travar o comando. Um processo que fugiu do grupo e
ficou com o pipe não segura a resposta (espera no máximo 0,5 s).

**D11. Mensagens com acento.**
O v1 escrevia sem acento. O v2 usa português com acento em tudo que o
usuário vê. O servidor em modo v1 tira os acentos, pra não quebrar scripts.

**D12. JSON estrito.**
Sem `NaN`, sem `Infinity` e sem chave repetida. Isso fecha o bug do `ts=NaN`
e tira a ambiguidade de qual valor vale numa chave repetida.

## Fase 2

**D13. Uma linha mágica antes dos frames.**
O controlador manda `RELAY/2\n` e o agente devolve a mesma. Isso dá erro
claro nos dois sentidos: um cliente v1 recebe uma linha JSON no formato dele
dizendo pra atualizar, e um controlador v2 falando com a versão antiga
avisa que "pode ser uma versão antiga". Custa 8 bytes.

**D14. Um pedido por conexão, várias respostas.**
Simplifica a amarração nonce + seq: cada conexão tem um nonce só e o seq
começa em 0. O cancelamento usa outra conexão. Conexão nova pelo Tailscale
custa pouco.

**D15. Um segredo por direção, e chave derivada com os dois nomes.**
O pedido diz "um segredo por par". Usei um por par **ordenado**
(controlador → agente). Assim "a G15 pode mandar na brasil" e "a brasil pode
mandar na G15" são decisões separadas, e revogar uma não mexe na outra. Por
cima disso, a chave de assinatura é derivada do segredo com os nomes das duas
pontas. Com isso, mesmo usando o mesmo segredo nas duas direções, um pedido
refletido de uma direção não vale na outra.

**D16. Controlador desconhecido e assinatura errada dão a mesma resposta.**
Sem assinatura, sem nonce, mesma mensagem. Ninguém de fora descobre quais
nomes o agente aceita.

**D17. Parâmetros chegam pelo stdin, como JSON.**
argv aparece no `ps` pra outros usuários da máquina (está no modelo de
ameaça). Variável de ambiente vaza pros filhos e tem limite de tamanho. O
stdin é privado. O agente escreve o JSON num arquivo temporário já apagado
e passa como stdin, pra nunca travar se o comando não ler. O helper é
`relay.command.params()`.

**D18. PARAMETROS inválido bloqueia o comando.**
Se o esquema não for um literal, ou tiver erro (tipo desconhecido, regex
quebrada, padrão que não passa na própria regra, chave desconhecida), o
comando fica bloqueado com o motivo. Ignorar faria o comando rodar sem as
checagens que o autor pediu. Chave desconhecida é erro pra pegar erro de
digitação (`obrigatoria` em vez de `obrigatorio`).

**D19. Texto de uma linha não aceita caracteres de controle.**
Só `multilinha: True` aceita `\n` e `\t`. Isso evita sequências de escape de
terminal e quebras de linha inesperadas em comandos que montam texto.

**D20. Config quebrado ou inseguro faz o agente recusar tudo.**
O agente recarrega o `relay.json` sozinho quando o arquivo muda. Se o novo
estiver com erro, ou com permissão aberta, ele não segue com o config
anterior: recusa todas as conexões até alguém consertar, e avisa no log. Um
config que ficou legível por outros pode ter vazado segredo.

**D21. Job continua se o controlador cair.**
O job tem timeout próprio, então não fica órfão (invariante 9). Se a
interface fechar sem querer, o job termina e o resultado fica no histórico
pra consultar com `status`. Pra parar, existe o `cancelar`.

**D22. Cada controlador só vê os próprios jobs.**
`status`, `cancelar` e `historico` filtram pelo controlador. Job de outro é
tratado como inexistente. Quem pegou um controlador não lê a saída dos jobs
de outro.

**D23. Chunks de saída "selados".**
O agente junta pedaços pequenos de saída pra mandar menos frames. Um pedaço
que alguém já recebeu nunca muda depois, então quem acompanha ao vivo nunca
perde texto.

**D24. A CLI cancela o job no primeiro Ctrl+C.**
O primeiro Ctrl+C manda `cancelar` e espera o fim (mostrando o que faltar).
O segundo sai na hora e o job pode continuar lá. A rede roda numa thread
separada, então o Ctrl+C nunca corta uma resposta no meio.

**D25. Argumentos soltos na CLI viram parâmetros, em ordem.**
`relay rodar g15 notificar "oi"` continua funcionando como no v1: o "oi" vai
pro primeiro parâmetro que faltar. Também dá pra usar `-p texto=oi`.

**D26. O comando `status` do exemplo é o único que os testes rodam.**
Ele só lê `/proc` e o disco. `notificar`, `travar_tela` e `abrir_url` nunca
rodam nos testes (mexem na tela). Os outros testes usam comandos falsos.

## Fase 3

**D27. READY=1 só depois de escutar, mas com prazo de 30 s.**
Com `Type=notify`, o `systemctl start` espera o READY. Se o Tailscale ainda
não subiu, o agente espera sozinho (1 s, 2 s, 4 s... até 30 s entre
tentativas) e mostra o motivo no `STATUS=`. Passados 30 s sem Tailscale, ele
manda READY mesmo assim, pro `systemctl` não ficar travado, e continua
tentando. Ele nunca sai com erro por falta de Tailscale, então não entra em
loop de reinício.

**D28. Watchdog com WatchdogSec=60.**
O agente manda `WATCHDOG=1` a cada 30 s enquanto a thread do servidor
estiver viva. Se ela morrer, o agente reabre o socket. Se o processo travar,
o systemd reinicia.

**D29. Se o IP do Tailscale mudar, o agente escuta no novo.**
A cada 30 s ele confere `tailscale ip -4`. Se mudou, fecha e reabre. Os jobs
que estavam rodando continuam (o gerenciador de jobs não é recriado).

**D30. Endurecimento do unit.**
Ficaram as opções que funcionam num unit de usuário e não quebram comandos de
desktop: `NoNewPrivileges`, `LockPersonality`, `RestrictRealtime`,
`RestrictSUIDSGID`, `RestrictNamespaces`, `KeyringMode=private`,
`SystemCallArchitectures=native`, um filtro de syscalls que tira só grupos
que um comando de desktop não usa (`@clock @cpu-emulation @debug @module
@obsolete @raw-io @reboot @swap`), `RestrictAddressFamilies` e `UMask=0077`.
Ficaram de fora, de propósito:
- `ProtectSystem`, `ProtectHome`, `PrivateTmp`, `PrivateDevices` e afins.
  Precisam de namespace de montagem. Num unit de usuário isso nem sempre
  funciona, `PrivateTmp` quebraria o X11 (`/tmp/.X11-unix`) e `ProtectHome`
  quebraria comandos. Um usuário comum já não escreve nessas pastas.
- `CapabilityBoundingSet`. O gerenciador do usuário não tem capabilities pra
  tirar, e a opção pode impedir o serviço de subir.
- `MemoryDenyWriteExecute`. Quebraria comandos que usam JIT (node, java).
O `systemd-analyze --user security --offline` dá nota MEDIUM porque ele pontua
como se fosse um serviço do sistema. Quase todos os itens vermelhos só
importam pra quem roda como root. Um teste confere que os itens que
escolhemos aparecem como ✓.

**D31. `RestrictNamespaces=yes` e `detach()`.**
Navegadores e Flatpak usam namespaces. Como os comandos herdam as regras do
agente, quem abre esses programas precisa usar `relay.command.detach()`. Ele
usa `systemd-run --user`, que cria uma unidade própria fora do agente (sem
essas regras e sem morrer quando o agente reinicia). O `abrir_url` já faz isso.

**D32. Auditoria com teto de disco.**
JSON lines em `~/.local/state/relay/auditoria.jsonl`, modo 600, pasta 700.
Gira em 1 MiB e guarda 5 arquivos, então usa no máximo uns 6 MiB. Registra
recusas (IP, assinatura, replay, permissão, whois), o início (`rodar`, com
parâmetros) e o fim (`fim`, com código, estado e duração). Parâmetros com
`segredo: True` aparecem como `***`. Texto longo é cortado em 200 caracteres.

**D33. A migração não sobrescrevia nada.**
Ela criava um config novo e deixava o antigo intacto, com backup, e só parava
o serviço antigo. Saiu depois (ver D70).

**D34. O instalador monta o pacote numa cópia temporária.**
Assim ele não precisa escrever na pasta do repositório nem deixa `build/` lá.
Se o setuptools do sistema servir, instala sem baixar nada
(`--no-build-isolation`).

**D35. O instalador usa `/usr/bin/python3`.**
É o Python do apt, o único que enxerga o PyGObject do apt. Dá pra trocar com
`RELAY_PYTHON`. Nesta máquina de nuvem o `/usr/bin/python3` é um 3.13 sem
PyGObject, então os testes usam o `/usr/bin/python3.12`.

**D36. Mira no Ubuntu 24.04.**
Não consegui ver as duas máquinas, então mirei no Ubuntu desta: 24.04, com
Python 3.12, GTK 4.14 e libadwaita 1.5. O código roda em Python 3.10 ou mais
novo. O instalador confere a libadwaita 1.5 e mostra a linha do apt se faltar.

**D37. `relay exemplos --atualizar`.**
Comandos no formato antigo continuam funcionando (o `ACEITA_ARGS` ainda vale). Pra trocar pelas versões novas,
com `PARAMETROS`, existe esse comando. Ele pergunta antes e guarda os antigos
como `.bak`.

## Fase 4

**D38. A interface não foi aberta nesta máquina.**
Instalar GTK 4 aqui precisaria de `apt` como root, e isso foi bloqueado (e
você pediu pra perguntar antes de sudo). Por isso a interface foi escrita
com cuidado em cima da libadwaita 1.5, e testada assim:
- toda a lógica (serviço, view-models, formulário, histórico) é Python puro,
  com 25 testes contra agentes de verdade;
- um teste confere que só uso widgets que existem na libadwaita 1.5;
- um smoke test abre o app numa tela falsa (Xvfb), roda um comando e fecha.
  Aqui ele pula. Nas suas máquinas, com os pacotes do apt, ele roda.
O primeiro teste de verdade da tela vai ser nas suas máquinas.

**D39. Lógica separada dos widgets.**
Só `app.py`, `window.py`, `pages.py`, `dialogs.py` e `widgets.py` importam o
`gi`. Um teste confere isso. Os view-models avisam a tela por um `Signal`
simples, e o serviço entrega os resultados da rede por um `dispatch` (na
tela, `GLib.idle_add`). A rede nunca roda na thread da tela.

**D40. Texto do agente nunca vira markup.**
As linhas do libadwaita interpretam markup por padrão. Um nome ou descrição
com `<` vindo do agente poderia quebrar a tela ou mudar o que aparece. O
helper `row()` desliga o markup, e o resto do texto é escapado. Um teste
confere que as telas usam o helper.

**D41. Formulário validado com o mesmo código do agente.**
A tela monta os `ParamSpec` a partir da lista do agente e valida com
`core.params`, o mesmo código que o agente usa. O erro aparece embaixo do
campo, antes de qualquer rede. O agente valida de novo de qualquer jeito.
Comandos antigos com `ACEITA_ARGS` ganham uma caixa "um argumento por linha".
Não existe campo de comando livre em lugar nenhum.

**D42. Texto obrigatório não pode ficar vazio.**
Antes o agente aceitava `""` num texto obrigatório. Agora recusa ("não pode
ficar vazio"). Vale pra CLI, interface e agente.

**D43. Histórico em SQLite, sem segredos.**
`~/.local/share/relay/historico.sqlite3`, modo 600. Guarda até 1000
execuções. Parâmetros marcados como `segredo` ficam como `***` e nunca são
preenchidos de novo no "Rodar de novo". Uma execução que estava rodando
quando a janela fechou aparece como "Sem resultado".

**D44. Notificação só pra job longo e janela sem foco.**
Acima de 10 s, se a janela não estiver em foco, o app manda uma notificação
do desktop. Clicar nela traz a janela de volta.

## Fase 5

**D45. Pareamento com SPAKE2 e um código de 8 números.**
Um código curto usado como senha direto seria quebrado offline por quem
visse a conversa. Com um PAKE (SPAKE2), quem escuta não aprende nada, e quem
finge ser uma das pontas só testa um código por tentativa. Com 3 tentativas
por janela de 2 minutos, a chance de acertar no chute é 3 em 100 milhões. O
segredo final é derivado dos dois lados e nunca passa pela rede.

**D46. SPAKE2 feito à mão, só com a biblioteca padrão.**
O pedido era usar só a stdlib. O `pow` do Python faz a conta no grupo de
2048 bits da RFC 3526 em poucos milissegundos. M e N saem de um hash
(ninguém sabe o log discreto deles). Os elementos recebidos são conferidos
(dentro do subgrupo, nem 0, nem 1, nem p−1). Não é auditado nem tempo
constante. Isso está escrito no `docs/SEGURANCA.md`, junto com o porquê de
ser aceitável aqui (uso raro, dentro do WireGuard do Tailscale).

**D47. Só números no código, em dois grupos de 4.**
Letras confundem fácil (0 e O, 1 e l, b e d). Números em dois grupos de 4
são mais fáceis de ler e digitar. O controlador aceita com espaço ou hífen.

**D48. Pareamento numa porta separada e num processo separado.**
A janela roda na porta 47801, aberta pelo `relay parear` (ou pela interface)
só por 2 minutos. O agente principal nem sabe que ela existe: o pareamento
grava o `relay.json` e o agente relê sozinho. Assim o código de pareamento
não fica exposto o tempo todo, e um bug nele não derruba o agente.

**D49. Cada pareamento vale pra uma direção.**
"A G15 deixou a brasil mandar" não quer dizer que a brasil deixou a G15. Pra
ter as duas direções, pareie duas vezes. Re-parear troca o segredo e mantém
as permissões de comandos que já existiam.

**D50. A instalação nas duas máquinas fica com você.**
Esta sessão roda numa máquina de nuvem e não alcança as suas. O README tem o
passo a passo. Não fiz nenhum SSH.

**D51. Prazo total pra receber o pedido.**
Antes, cada `recv` tinha 15 s, mas não havia limite pro pedido inteiro.
Quem mandasse um byte a cada 14 s seguraria uma das 32 vagas quase pra
sempre. Agora a linha mágica e o pedido precisam chegar em 15 s no total.
Na janela de pareamento (que atende uma conexão por vez) o prazo total é de
10 s por conexão, pra ninguém travar o pareamento com uma conexão lenta.

## Revisão de segurança

Um revisor independente leu o código depois da fase 5. Tudo que ele achou e
eu confirmei foi corrigido, com um teste cada (`test_review_*` e outros).

**D52. No máximo 4 conexões abertas por IP.** Um aparelho do tailnet que
reconectava sem parar ocupava as 32 vagas. Agora precisa de 8 IPs.

**D53. Segredo e código nunca no argv.** `--segredo <hex>` e `--codigo`
deixavam o segredo visível no `ps` pra outros usuários da máquina. Agora só
pelo terminal (sem eco) ou pelo stdin. `--segredo -` continua funcionando.

**D54. Texto de fora não forja log nem apaga auditoria.** O erro de chave
repetida não repete mais a chave (que vinha do atacante). Todo texto de fora
é cortado em 200 caracteres e tem controle escapado antes do log. Recusas
anônimas vão pra auditoria no máximo 30 por minuto.

**D55. Regex do PARAMETROS num processo à parte, com 1 s.** O `re` segura o
GIL: uma regex com backtracking congelava o agente inteiro, inclusive os
timeouts dos outros jobs e o watchdog. Num processo à parte, só aquele pedido
falha. Custa uns 30 ms por parâmetro com regex.

**D56. `whois` só confia no `Node.Name` do próprio tailnet.** O
`Hostinfo.Hostname` é o aparelho que diz, e qualquer um podia dizer que se
chamava "cass-dell-brasil". Agora vale o nome dado pelo servidor de controle,
com o domínio deste tailnet (lido do `tailscale status --json`).

**D57. Mesmo trabalho pra nome desconhecido.** Antes o agente recusava um
nome desconhecido antes de calcular o HMAC, e o tempo dizia quais nomes
existiam. Agora ele calcula com uma chave qualquer.

**D58. Backups com modo 600 desde o primeiro byte, e pasta do config
conferida.** O `shutil.copy2` criava a cópia aberta e só depois mudava o
modo. Agora usa a mesma escrita privada do resto. O `relay.json` também é
recusado se a pasta dele for gravável por outros.

**D59. `CONFIRMAR` também no agente.** O agente exige `"confirmado": true`.
Não para quem pegou o controlador, mas garante que todo controlador pergunte.

**D60. Histórico de jobs separado por controlador, e teto no controlador.**
Um controlador não empurra mais os resultados do outro pra fora. O
controlador guarda no máximo 1 Mi caracteres de saída por job, contra um
agente que mande saída sem fim.

**D61. `--` no exemplo `notificar`.** Sem ele, um texto começando com `-`
virava opção do `notify-send`.

**Ficou como limite conhecido** (está no `docs/SEGURANCA.md`): processo que
sai do grupo de propósito (`setsid`) não é morto no timeout. Um cgroup por
job resolveria, e ficou como melhoria futura.

## Nome novo e branches

**D62. O projeto se chama Relay.**
Você não gostou do nome antigo. "Relay" é inglês, genérico e diz o que o
programa faz: repassa um pedido de uma máquina pra outra. Mudou tudo que tem
nome: o pacote (`relay`), os comandos (`relay`, `relay-agent`, `relay-gui`),
o app (`dev.cass.Relay`), o serviço (`relay-agent.service`), as pastas
(`~/.config/relay`, `~/.local/state/relay`, `~/.local/share/relay`), o arquivo
de config (`relay.json`), as variáveis de ambiente (`RELAY_*`) e os rótulos do
protocolo (`RELAY/2`, `RELAY-PAR/1`). Os textos da tela e das mensagens
continuam em português, como no pedido original.

**D63. Nada usa mais o nome antigo.**
Num primeiro momento a migração ainda procurava as pastas e o serviço com o
nome antigo. Isso saiu (ver D70).

**D64. Comandos ficam em `~/.config/relay/comandos`.**
Pra trazer comandos de uma instalação antiga, é só copiar os `.py` pra lá
com modo 600.

**D65. Ajuda dos comandos em inglês: `relay.command`.**
`params()`, `caller()` e `detach()` (antes `parametros()`, `quem_chamou()` e
`soltar()`). O pedido original era identificadores em inglês, e a troca de
nome foi a hora certa de acertar isso. O comando recebe `RELAY_CALLER`,
`RELAY_COMMAND` e `RELAY_COMMAND_FILE` no ambiente.

**D66. Branches com nome que diz o que fazem, mergeados no `main`.**
`feature/...` pra coisa nova, `fix/...` pra correção, `refactor/...` pra
reorganizar, `docs/...` e `chore/...` pro resto. Quando o trabalho termina e
os testes passam, o branch é mergeado no `main` (com `--no-ff`, pra ficar
claro no histórico o que veio junto). A regra está no `CLAUDE.md`, pra valer
também nas próximas sessões.

## Máquinas novas

**D67. HP como thinker, G15 como gêmeo, nas duas direções.**
O par agora é `cass-hp-elitedesk-800-g6` (thinker) e `cass-dell-g15-5511`
(gêmeo), e os papéis podem trocar. Não criei um conceito de "papel" no
código: o Relay já é simétrico. As duas máquinas instalam agente e interface
e são pareadas nas duas direções. Quem manda é só quem está usando o
controlador naquela hora. O gêmeo "manda informação pro thinker" de dois
jeitos: a saída dos comandos que o thinker roda volta ao vivo, e o gêmeo pode
rodar comandos no thinker por conta própria.

**D68. `relay agente permissoes`.**
Com as duas direções abertas, vale poder limitar o que cada uma roda na outra
sem abrir a interface (a HP pode ficar sem tela). Sem lista, mostra. Com
lista, troca. Com `*`, libera tudo. Vale na hora, porque o agente relê o
config sozinho.

**D69. A `cass-dell-brasil` sai do par.**
As duas máquinas começam do zero com `relay init` e são pareadas por código.
Nenhuma confiança antiga é trazida.

## Limpeza do nome antigo

**D70. Saem a migração, o script original e os testes dele.**
Você pediu pra tirar o nome antigo de tudo. Ele só sobrava no suporte à
versão antiga: o script original, os testes que fixavam o comportamento dele,
a migração e uma variável de ambiente de compatibilidade. Como a HP é nova e
a brasil saiu do par, a migração quase não servia mais. Ela saiu inteira, com
o `relay migrar`. As duas máquinas começam com `relay init` e pareamento por
código. Os bugs achados na versão antiga continuam descritos no D03, e os
testes de ataque do v2 cobrem as mesmas garantias. Um teste confere que o
nome antigo não volta em nenhum arquivo do repositório.

## Correções depois da instalação

**D71. Porta ocupada não deixa o serviço preso em "activating".**
Na HP, outro programa já usava a porta 47800. O agente ficava tentando de novo
sem mandar READY, e o systemd acabaria matando ele por demora e reiniciando
em loop. Agora, igual à espera do Tailscale, ele manda READY depois de 30 s e
continua tentando. O STATUS explica que outro programa usa a porta e mostra o
comando pra ver qual (`ss -ltnp | grep 47800`). Quando a porta libera, ele
escuta sozinho.

## Comandos mais fáceis

**D72. Um pareamento libera as duas direções.**
Na primeira instalação, o pareamento saiu do lado errado: a G15 ficou mandando
na HP, e não o contrário. Como os papéis podem trocar, agora um pareamento só
cria os dois segredos (um pra cada direção) e grava as duas máquinas dos dois
lados. Não existe mais "lado certo" pra rodar cada comando. A mesma pessoa vê o
código numa máquina e digita na outra, então ela está nas duas e quer as duas
juntas. Pra limitar uma direção depois, existe `relay agente permissoes`. A
versão do pareamento subiu pra `RELAY-PAR/2`, e versões diferentes dão um erro
claro pedindo pra atualizar as duas.

**D73. Nome curto no pareamento.**
`relay parear g15` pergunta ao Tailscale (`tailscale status --json`) quais
máquinas existem e acha a que bate. Se duas baterem, ele pede mais do nome. O
`--com` antigo ainda funciona, mas sumiu da ajuda. A máquina que mostrou o
código guarda o nome do Tailscale da outra (ou o IP de onde ela veio, se não
achar o nome).

**D74. `relay` sozinho mostra um resumo.**
Quem é esta máquina, com quem ela está pareada, em que direção, se a outra está
online (um ping rápido) e os comandos mais usados. É o primeiro comando a
rodar quando algo parece estranho.

**D75. `relay-gui --checar`.**
A primeira vez que a interface abriu de verdade (na HP) ela quebrou num método
que não existe no GTK 4 (`set_hscrollbar_policy`, o certo é `set_policy`).
Como eu não consigo abrir janela na máquina de nuvem, criei um modo que monta
todas as telas com dados de exemplo, passa por todos os estados (carregando,
erro, rodando, terminou, cancelado...) e lista tudo que quebrou de uma vez,
sem mostrar janela, sem abrir pareamento e sem mexer na área de transferência.
Ele usa um app id separado, pra não se misturar com uma janela já aberta.

**D76. Links abrem pelo portal do desktop.**
O `abrir_url` dizia "deu certo" mas nada abria na G15. O `xdg-open` rodava numa
unidade do systemd, abria o navegador e saía na hora; o systemd então matava
tudo que ficou na unidade, inclusive o navegador novo ou o pedido pro navegador
já aberto. E se o `systemd-run` falhasse, o plano B rodava dentro das regras do
agente, onde um navegador em snap não abre (o `NoNewPrivileges` impede o
`snap-confine`), e ninguém via o erro. Agora `open_uri()` pede pro portal do
desktop (xdg-desktop-portal) abrir o link: quem abre é a sessão gráfica, com o
navegador padrão, fora do agente. Se o portal não responder, cai no `xdg-open`
com `KillMode=process` (o systemd não mata o que o lançador deixou rodando), e
qualquer falha aparece na saída do comando. O resultado diz por qual caminho
abriu.

**D77. Testes nunca enxergam a sessão gráfica de verdade.**
Rodando na sua máquina, os testes poderiam achar o barramento do desktop pelo
`XDG_RUNTIME_DIR` ou pegar o `DISPLAY` pelo `systemctl --user`. Agora o
`conftest.py` aponta o `XDG_RUNTIME_DIR` pra uma pasta temporária e desliga a
leitura do ambiente da sessão (`RELAY_NO_SESSION_ENV`).

## Screenshot e arquivos de volta

**D78. Um comando pode devolver um arquivo binário (artefato).**
Até aqui um comando só devolvia texto (stdout/stderr), com teto de 256 KiB.
Uma imagem é binária e maior. Agora o agente dá ao comando um caminho em
`RELAY_OUTPUT_FILE`; se o comando escrever um arquivo ali, o agente lê quando
o comando termina, descobre o tipo pelos primeiros bytes (não confia no que o
comando diz), e manda de volta em frames `artefato`, em base64, antes do
`fim`. O controlador junta os pedaços. É genérico: serve pra imagem, PDF, o
que for.

**D79. Limites do artefato: 8 MiB por arquivo, 64 MiB na memória do agente.**
Um print cabe de sobra em 8 MiB. Acima disso o agente recusa e diz o motivo
(`artefato_erro`), sem derrubar o comando. O agente guarda os artefatos na
memória pra o `status` poder reenviar depois, com teto total de 64 MiB,
soltando os mais antigos. O arquivo é lido com `O_NOFOLLOW` e precisa ser
arquivo comum: um comando bugado não faz o agente devolver outra coisa por um
link simbólico.

**D80. Em timeout ou cancelamento, o artefato é descartado.**
Aí o arquivo pode estar pela metade. Com código != 0 (o comando rodou até o
fim) o artefato é lido normalmente, porque pode ser útil.

**D81. Screenshot sem notificação nem atalho de teclado.**
No GNOME (X11 ou Wayland) o comando usa a API do próprio shell
(`org.gnome.Shell.Screenshot`, com flash desligado), que escreve o PNG direto,
sem piscar a tela e sem notificação. Não usa o portal (que às vezes pede
confirmação) nem atalho de teclado. Fora do GNOME, tenta `grim`,
`gnome-screenshot -f`, `spectacle`, `scrot`, `maim` e o ImageMagick, nessa
ordem, e diz qual funcionou. Se nenhum existe, sugere instalar o
`gnome-screenshot`.

**D82. A imagem aparece na interface e salva na CLI.**
Na interface, a tela de executar mostra a imagem assim que ela chega
(`Gtk.Picture` + `Gdk.Texture`), com botão de salvar. Pela linha de comando,
`relay rodar <maq> screenshot --salvar <pasta>` grava o arquivo com nome
automático. O `relay-gui --checar` monta a tela com uma imagem de exemplo.

**D83. O install.sh traz exemplos novos sozinho.**
Antes, um exemplo novo (como o screenshot) só chegava rodando `relay exemplos`
à mão em cada máquina. Agora o install.sh copia os exemplos que faltam depois
de garantir o config, sem tocar nos comandos que você já tem nem no config
(nada de re-parear). `git pull && ./scripts/install.sh` basta. O
`relay exemplos` continua existindo pra quem quer rodar só isso, e o
`--atualizar` troca os antigos pelas versões novas.

**D84. Print silencioso só em X11; no Wayland o comando explica.**
A API de screenshot do GNOME (`org.gnome.Shell.Screenshot`) é bloqueada pra
programas de fora no GNOME novo em Wayland ("Screenshot is not allowed"), e o
portal pede confirmação. Não existe print sem nenhum aviso pra um programa de
fora em Wayland, por decisão de segurança do próprio Wayland. Então o comando
detecta a sessão: em X11 usa scrot/maim/gnome-screenshot/ImageMagick/ffmpeg
(silenciosos); em Wayland tenta a API do shell e o grim, e se falhar explica
que pra print silencioso é preciso usar a sessão "Ubuntu on Xorg". A mensagem
de erro mostra a sessão e o que instalar.

**D85. Visualizador de imagem maior, com tamanho real.**
A prévia da imagem era limitada a 320px de altura (o arquivo salvo sempre foi
na resolução cheia). Agora a imagem fica numa área rolável mais alta, a tela
de executar é mais larga (1200px), e um botão alterna entre "caber na largura"
e "tamanho real" (100%, com rolagem nos dois sentidos). O 100% é o que vai
servir pra selecionar uma região pixel a pixel no próximo passo.

## Ler texto da tela (OCR)

**D86. OCR pela API do Claude, chamada do controlador.**
A leitura do texto roda na máquina que usa a interface (a HP), que já recebe o
print. A chave da API fica só nela, num `~/.config/relay/claude.json` modo 600,
separada dos segredos de pareamento. A G15 nunca vê a chave. Em runtime o
projeto só usa a biblioteca padrão, então a chamada vai por HTTPS com `urllib`,
sem o SDK; o transporte é injetável pros testes não tocarem na rede.

**D87. Modelo padrão opus-5-5, configurável.**
Sigo o padrão da referência da API (opus-5-5). Como é uma chamada por recorte e
pode ser bastante, deixei o modelo configurável nas Configurações, pra trocar
por um Sonnet/Haiku mais barato sem mexer no código.

**D88. Recorte na HP, com GdkPixbuf.**
O print vem inteiro da G15 e o recorte é feito na HP, arrastando um retângulo
sobre a imagem (uma camada de desenho por cima do `Gtk.Picture`). As
coordenadas da tela viram pixels da imagem pela escala. O recorte sai com
`GdkPixbuf.new_subpixbuf`, sem depender do PIL. Nada de imagem vai pra rede
além do recorte que você escolheu.

**D89. Bloco de texto que acumula e libera.**
O `CaptureVM` (puro, testado) junta o texto de cada recorte num bloco, separado
por linha em branco, e dá pra editar à mão. "Liberar" devolve o bloco inteiro
(copia e oferece salvar) e começa um novo, vazio. É o ciclo que você descreveu:
vários recortes, um release, repete.

**D90. Corrige o zoom da imagem e um teste contra métodos do GTK 3.**
O `--checar` pegou `set_hscrollbar_policy` (não existe no GTK 4; o certo é
`set_policy`) no zoom da imagem, antes de aparecer na tela real. Além de
corrigir, adicionei um teste que procura nomes de métodos só do GTK 3
(`set_hscrollbar_policy`, `show_all`, `get_style_context`...) nos arquivos de
tela, pra pegar essa classe de erro sem precisar de GTK instalado.

**D91. scrot pra não piscar a tela.**
Em X11 no GNOME 46 a API de screenshot do shell é bloqueada pra programas de
fora (como em Wayland), então o comando cai no `gnome-screenshot`, que pisca a
tela ao salvar e não tem como desligar isso. O `scrot` (e o `maim`, o `import`)
tira o print sem piscar, e o comando já prefere eles. Quando só o
`gnome-screenshot` existe e é usado, o comando avisa pra instalar o `scrot`.

## Ler texto da tela, parte 2

**D92. Botão "Ler o texto (OCR)" direto na imagem.**
A tela de OCR era aberta por um ícone de câmera no topo da lista de comandos,
fácil de não achar (o usuário rodava o comando `screenshot` e via só a imagem).
Agora, quando um comando devolve uma imagem, aparece um botão "Ler o texto
(OCR)" na própria imagem, que abre a tela de recorte já com a imagem carregada.
O ícone no topo continua, pra começar do zero.

**D93. O instalador atualiza sozinho os exemplos intactos.**
O `install.sh` só criava exemplos que faltavam, então uma correção num exemplo
que já existia (o screenshot) nunca chegava. Agora o Relay guarda um hash de
cada exemplo que escreve (`.nome.py.sha`). Na instalação, um exemplo cujo
conteúdo ainda bate com esse hash (você não editou) é atualizado sozinho quando
a versão nova muda; um que você editou fica intacto (só troca com
`--atualizar`, que faz backup). Instalações antigas não têm o hash, então o
primeiro `--atualizar` é manual; depois disso é automático.

**D94. O recorte vira um só DrawingArea, e os botões perdem os números.**
A área de recorte era um `Overlay` com a imagem embaixo e um `DrawingArea`
transparente por cima pra receber o arrasto. O `DrawingArea` por cima ficava com
tamanho zero, então não dava pra arrastar nada: não aparecia retângulo de
seleção. Agora é um só `DrawingArea` que desenha a própria imagem (via Cairo) e
o retângulo, e recebe o arrasto. O que o mouse toca e o que é desenhado usam a
mesma coordenada. A imagem também ficou maior (520 px de altura) e mostra uma
dica escrita por cima ("Arraste o mouse sobre o texto…") enquanto nada está
selecionado. Os botões "1 · Tirar print" e "3 · Ler recorte" (sem um "2 ·", que
era o arrasto) viraram só "Tirar print" e "Ler recorte", e o "Ler recorte" ficou
logo abaixo da imagem, do lado de onde a seleção é feita.

**D95. A imagem do recorte é um `Gtk.Picture`, e a seleção fica numa camada só
de desenho.**
Desenhar a imagem na mão no `DrawingArea` (com `Gdk.cairo_set_source_pixbuf`)
não aparecia na tela: a caixa ficava preta e não dava pra recortar. Voltamos a
mostrar a imagem com `Gtk.Picture` (o mesmo jeito que já funciona na tela do
print) e pusemos por cima um `DrawingArea` transparente que só desenha o
retângulo azul. Essa camada não pega clique (`can_target = False`), então o
arrasto vai direto pro `Picture`; os dois usam a mesma coordenada. O cursor vira
uma cruz em cima da imagem. Tem um botão "Tamanho real" pra ver em 100% e mirar
num texto pequeno (a área rola). A seção da imagem só aparece depois que tem um
print, pra tela começar limpa.

**D96. Detalhe comprido de comando vai pra um (i), e a saída encolhe.**
O texto longo de ajuda (ex.: a explicação de X11/Wayland do screenshot) enchia o
topo da tela de rodar. Agora ele fica atrás de um botão (i) do lado do nome do
comando. E a caixa de saída deixou de ter altura fixa de 260 px (uma saída de
uma linha virava um retângulo preto enorme): cresce com o conteúdo até um teto e
só então rola.

**D97. O recorte não usa cairo: o retângulo é widget.**
Desenhar com cairo (`DrawingArea` + draw func) quebrava na máquina da Cass com
`Couldn't find foreign struct converter for 'cairo.Context'` (falta o
`python3-gi-cairo`) e chegava a dar segfault. Em vez de pedir pra instalar outro
pacote, tiramos o cairo de vez (era o único lugar que usava). Agora o retângulo
de seleção, o rótulo com o tamanho em pixels e a dica são widgets normais
(`Gtk.Box`, `Gtk.Label`) postos por cima da imagem num `Gtk.Fixed`, com um CSS
pequeno pra borda e o fundo. O arrasto mostra o retângulo na hora, com a borda
azul e o tamanho, pra ter feedback de verdade enquanto seleciona. Só precisa da
biblioteca padrão e do PyGObject, como manda o `CLAUDE.md`.

**D98. Renderizador de software por padrão (`GSK_RENDERER=cairo`).**
O `relay-gui` dava segfault (piscava a tela e caía) ao redimensionar a janela ou
ao clicar depois de mostrar um print grande. É o renderizador GL do GTK 4, que
quebra em várias placas e drivers (a G6 e a G15 são híbridas). O `app.py` agora
define `GSK_RENDERER=cairo` (software, estável, usa a libcairo do sistema) com
`setdefault`, então quem quiser GL ainda consegue pela variável de ambiente.

**D99. A janela lembra o tamanho, e a tela de OCR cresce junto.**
A janela abria pequena (820x720) e a imagem do recorte ficava num tamanho fixo
mesmo esticando a janela. Agora o padrão é 1040x860, o tamanho é salvo em
`janela.json` ao fechar e restaurado ao abrir, e a tela "Ler tela" deixou de ter
rolagem vertical por fora: a imagem é `vexpand` e ocupa o espaço que sobra,
crescendo com a janela sem distorcer (continua `CONTAIN`).

**D100. Escolha do modo de OCR vira radio em vez de dropdown.**
"Ler o texto" e "Perguntar ao Claude" eram um `Adw.ComboRow` (dropdown). Viraram
dois `Gtk.CheckButton` agrupados (radio), que mostram as duas opções de uma vez.
O campo da pergunta só aparece no modo "Perguntar".

**D101. O recorte é grampeado na borda da imagem (fim de um segfault).**
Clicar em "Ler recorte" dava segfault. O `crop_png` recorta com
`GdkPixbuf.new_subpixbuf`, que lê fora da memória (e o programa cai) se o
retângulo passar da borda — `x + largura > largura da imagem` ou
`y + altura > altura`. Por causa do arredondamento, uma seleção encostando na
borda (tipo o título lá embaixo) estourava por um pixel. Agora a conta de
seleção → pixels mora em `gui/geometry.py` (sem GTK, dá pra testar) e grampeia
tudo dentro de `[0, largura) x [0, altura)`. Tem teste com 5000 seleções
aleatórias tentando estourar as bordas.

**D102. Desliga o OpenGL no GTK (`GDK_DEBUG=gl-disable`) — a causa real do
segfault.**
O `relay-gui` caía com segfault na máquina da Cass (ao recortar, ao
redimensionar). A pilha de queda não vinha (faulthandler só mostrava o
cabeçalho de uma thread, sem frames Python) e o mesmo código roda liso num GTK
4.14 igual, sob Xvfb — os dois sinais de que o tombo é no driver de vídeo, não
no código. Pôr só `GSK_RENDERER=cairo` (D98) não resolveu: mesmo assim o GDK
cria um contexto GL (dá pra ver os avisos de libEGL), e é nele que a placa cai.
`GDK_DEBUG=gl-disable` tira o OpenGL do GDK de vez (os avisos somem) e o GSK usa
o renderizador de software sozinho. Fica ligado por padrão com `setdefault`,
então quem tiver uma placa boa pode reativar pela variável.

**D103. Recorte sem `Gtk.Fixed` (faxina).**
De quebra, o retângulo e o rótulo de tamanho saíram do `Gtk.Fixed` e viraram
filhos diretos do `Gtk.Overlay`, posicionados por margem. É o jeito mais comum e
evita o `Gtk.Fixed`, que tem alocação de tamanho esquisita. Não era a causa do
segfault (o código com `Fixed` roda liso no teste), é só pra simplificar.

**D104. A causa real do segfault: a textura do recorte vinha de
`new_for_pixbuf`.**
Um `gdb` na máquina da Cass (GTK mais novo que o daqui, Python 3.14) mostrou a
pilha de verdade: o tombo é no `gsk_renderer_render` →
`gdk_texture_downloader_download_into`, ou seja, na hora de desenhar a imagem a
textura é lida fora da memória. A tela do recorte montava a textura com
`Gdk.Texture.new_for_pixbuf`, que embrulha a memória do pixbuf (com rowstride
"folgado"); no GTK novo da Cass isso vira uma leitura fora do limite. A tela do
print (`ImageView`), que monta a textura com `new_from_bytes` (decodifica o PNG
direto), sempre funcionou lá. Agora o recorte usa `new_from_bytes` também (e
guarda o pixbuf só pra recortar). Isso explica por que caía nos dois
renderizadores (GL e cairo): a textura é que estava ruim, não o renderizador.
As tentativas de desligar GL por `GDK_DEBUG` (D102) foram tiro no escuro — o GTK
novo da Cass nem reconhece esses flags —, então saíram; ficou só o
`GSK_RENDERER=cairo` e o `GTK_MEDIA=none`, por garantia.

**D105. Perguntar sem pergunta, e um rodinha enquanto o Claude trabalha.**
No modo "Perguntar", deixar a pergunta em branco antes dava erro ("digite a
pergunta") e parecia que nada acontecia. Agora, sem pergunta, manda um prompt
padrão (`DEFAULT_ASK_PROMPT`) que pede pro modelo fazer o melhor com o recorte:
responder, explicar ou completar código. O campo virou "(opcional)". E a tela de
OCR ganhou um `Gtk.Spinner` do lado do status, que gira enquanto tira o print,
lê o texto ou pergunta, pra não parecer travado. (`Gtk.Spinner`, não
`Adw.Spinner`, que é de libadwaita mais nova.)

**D106. Volta a rolagem na tela de OCR, e a caixa de texto tem teto.**
Tirar a rolagem vertical pra imagem crescer (D99) deu ruim: com bastante texto
no bloco, a caixa crescia e empurrava os botões (Copiar, Liberar, Limpar) pra
fora da janela, sem como chegar neles. Voltou a rolagem por fora (a página
inteira rola, os botões sempre dão pra alcançar), a imagem ficou num tamanho
fixo generoso (440 px, e o "Tamanho real" mostra em 100%), e a caixa "Texto
juntado" virou uma área de altura limitada (200–320 px) que rola por dentro, em
vez de crescer sem parar. Usabilidade na frente do "a imagem acompanha a
janela".

**D107. Resposta da pergunta vem sem markdown.**
No modo "pergunta" o Claude respondia com markdown (**negrito**, ## título,
crases). Como o bloco vai pra OCR, autodigitação etc., a resposta tem que ser
texto puro. Agora a pergunta leva junto um pedido pra responder sem markdown
(`NO_MARKDOWN`), e `strip_markdown()` ainda tira os marcadores que escaparem. O
OCR NÃO passa por isso: é o texto literal da tela e fica igual.

**D108. Parâmetro de texto de várias linhas aceita bloco grande, e o comando
`autodigitar`.**
Pra mandar o bloco pro G15 digitar (sem arquivo e sem área de transferência), o
jeito do Relay serve de luva: os parâmetros vão pelo stdin do comando (não
aparecem nem no `ps`). O comando `autodigitar` recebe o bloco no parâmetro
`texto` e repassa pro programa `autotyper` (que a Cass instala à parte) também
pelo stdin. Como o bloco junta vários recortes, um `texto` de uma linha (teto de
4096) era pouco: criamos o teto `MAX_MULTILINE` (32768) só pra texto
`multilinha`, bem abaixo do frame de pedido (64 KiB), que é o limite real. Texto
de uma linha continua preso em 4096. Tem teste que tenta estourar os dois tetos.
A tela de OCR ganhou o botão "Digitar em <máquina>", que manda o bloco pro
comando `autodigitar` da máquina.

**D109. O bloco vai cifrado pro `tttttt` (openssl AES-256-CBC + PBKDF2).**
O programa que digita chama `tttttt` e só aceita texto cifrado. O controlador
cifra o bloco antes de mandar, com a senha compartilhada, chamando o `openssl`
do sistema (`core/autotype.py`), na receita exata que o `tttttt` exige:
`openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -salt -a -A`. O
`tttttt` do outro lado abre com a mesma senha
(`openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -a -A -pass pass:SENHA`).
O `-A` deixa o base64 numa linha só, e o `-iter 600000` é obrigatório (o `tttttt`
recusa sem ele). A senha vai pro
openssl por variável de ambiente (`env:`), nunca por argumento, pra não aparecer
no `ps` de outros usuários (um teste confere isso). Ela fica só na máquina de
quem cifra, num `tttttt.json` modo 600, e é posta nas Configurações. O comando
`autodigitar` nem vê a senha: só repassa o texto cifrado pro `tttttt`, que
decifra. Em tempo de execução, só a biblioteca padrão e o openssl do sistema
(como o scrot do screenshot).
