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
então o caminho real vai em `PONTE_COMANDO_ARQUIVO`.

**D09. O grupo de processos morre quando o comando acaba.**
No v1, um filho deixado pelo comando vivia pra sempre, sem timeout. Agora,
quando o processo principal termina, o resto do grupo recebe SIGTERM e,
3 s depois, SIGKILL. O líder só é colhido depois disso (`waitid` com
`WNOWAIT`), então o número do grupo nunca é reusado no meio do caminho. Quem
quer deixar um programa aberto (um navegador) solta ele de propósito com
`ponte.comando.soltar()`. Isso fecha a invariante 9 sem exceção.

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
O controlador manda `PONTE/2\n` e o agente devolve a mesma. Isso dá erro
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
`ponte.comando.parametros()`.

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
O agente recarrega o `ponte.json` sozinho quando o arquivo muda. Se o novo
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
`ponte rodar g15 notificar "oi"` continua funcionando como no v1: o "oi" vai
pro primeiro parâmetro que faltar. Também dá pra usar `-p texto=oi`.

**D26. O comando `status` do exemplo é o único que os testes rodam.**
Ele só lê `/proc` e o disco. `notificar`, `travar_tela` e `abrir_url` nunca
rodam nos testes (mexem na tela). Os outros testes usam comandos falsos.
