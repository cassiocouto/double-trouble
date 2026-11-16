# Decisões

Cada decisão importante, com o porquê em poucas linhas. A mais nova fica no
fim de cada fase.

## Fase 0

**D01. Trabalho feito numa máquina de nuvem, não na cass-dell-brasil.**
O `hostname` aqui é `vm` (Ubuntu 24.04, Python 3.12 do sistema). Por isso
nada aqui toca no `~/.config/relay` real, e não dá pra acessar a G15. A
instalação nas duas máquinas fica pra você, com os passos no README.

**D02. O `ponte.py` original fica em `legacy/v1_original.py`, sem mudanças.**
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
dizendo pra atualizar, e um controlador v2 falando com o ponte.py antigo
avisa que "pode ser o v1". Custa 8 bytes.

**D14. Um pedido por conexão, várias respostas.**
Simplifica a amarração nonce + seq: cada conexão tem um nonce só e o seq
começa em 0. O cancelamento usa outra conexão. Conexão nova pelo Tailscale
custa pouco.

**D15. Um segredo por direção, e chave derivada com os dois nomes.**
O pedido diz "um segredo por par". Usei um por par **ordenado**
(controlador → agente). Assim "a G15 pode mandar na brasil" e "a brasil pode
mandar na G15" são decisões separadas, e revogar uma não mexe na outra. Por
cima disso, a chave de assinatura é derivada do segredo com os nomes das duas
pontas. Com isso, mesmo o config migrado do v1 (mesmo segredo nas duas
direções) não aceita um pedido refletido de uma direção na outra.

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

**D33. A migração não sobrescreve nada.**
O v2 usa um arquivo novo, `relay.json`. O `config.json` do v1 fica intacto,
e ganha uma cópia de backup do lado. O `ponte.service` antigo é parado e
desabilitado, mas o arquivo fica. Voltar pro v1 é
`systemctl --user enable --now ponte.service`. O `install.sh` mostra o plano
e pergunta antes. Sem terminal (e sem `--sim`), ele pula a migração.

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

**D37. `relay exemplos --atualizar` pra quem migrou.**
A migração copia os comandos do v1 sem mudar nada neles. Os exemplos antigos
continuam funcionando (o `ACEITA_ARGS` ainda vale). Pra trocar pelas versões novas,
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

**D63. Só o que é do v1 continua com o nome antigo.**
A migração ainda procura `~/.config/ponte/config.json` e o `ponte.service` que
roda `ponte.py servir`, porque é isso que está nas suas máquinas. O script
original foi pra `legacy/v1_original.py`, sem mudar uma linha.

**D64. A migração copia os comandos pra pasta nova.**
Com a pasta de config nova, os comandos de `~/.config/ponte/comandos` são
copiados (modo 600) pra `~/.config/relay/comandos`. Os originais ficam onde
estão, então voltar pro v1 continua funcionando. Um comando que já existe na
pasta nova nunca é sobrescrito.

**D65. Ajuda dos comandos em inglês: `relay.command`.**
`params()`, `caller()` e `detach()` (antes `parametros()`, `quem_chamou()` e
`soltar()`). O pedido original era identificadores em inglês, e a troca de
nome foi a hora certa de acertar isso. O comando recebe `RELAY_CALLER`,
`RELAY_COMMAND` e `RELAY_COMMAND_FILE` no ambiente, e também `PONTE_DE`, só
pra não quebrar comandos antigos do v1 que liam essa variável.

**D66. Branches com nome que diz o que fazem, mergeados no `main`.**
`feature/...` pra coisa nova, `fix/...` pra correção, `refactor/...` pra
reorganizar, `docs/...` e `chore/...` pro resto. Quando o trabalho termina e
os testes passam, o branch é mergeado no `main` (com `--no-ff`, pra ficar
claro no histórico o que veio junto). A regra está no `CLAUDE.md`, pra valer
também nas próximas sessões.
