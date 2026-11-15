# Segurança do ponte

Este documento diz do que o ponte se protege, como, e o que fica de fora.
Cada regra tem testes que tentam quebrar ela (em `tests/agent/test_attacks.py`
e nos outros arquivos citados).

## O que estamos protegendo

O agente roda programas no seu usuário, na sua sessão gráfica. Quem conseguir
mandar o agente rodar qualquer coisa tem o seu usuário inteiro. Então o
objetivo principal é:

> **Ninguém roda nada além dos comandos que você colocou na pasta, com os
> parâmetros que eles aceitam.**

Depois disso, em ordem: não vazar os segredos, não vazar a saída dos
comandos, e não deixar ninguém derrubar o agente fácil.

## Quem ataca

| Atacante | Tem o quê | O pior que deve conseguir |
|---|---|---|
| **Outro dispositivo do tailnet** | Rede até a porta do agente | Ver que o ponte existe ali, e ocupar até 4 conexões por IP (32 no total) |
| **Outro usuário local** da mesma máquina | Uma conta comum, sem root | Ver que o agente existe. Ver o `argv` dos comandos antigos (com `ACEITA_ARGS`). Atrapalhar um pareamento em andamento (gastando as 3 tentativas) |
| **Quem pegou o controlador** (o notebook, o config) | O `ponte.json` do controlador, com o segredo da direção | Rodar os comandos que você já aprovou pra esse controlador, com os parâmetros que eles aceitam. Ver os jobs que esse controlador rodou |
| Alguém com **root** em qualquer das duas máquinas | Tudo | Tudo. Fora do escopo |

O Tailscale é a primeira camada: só máquinas do seu tailnet chegam na porta,
e o tráfego vai cifrado pelo WireGuard. O ponte **não confia** só nisso. Ele
assume que um dispositivo do tailnet pode ser hostil.

## As 11 regras e como cada uma é garantida

**1. Só arquivos da pasta de comandos.**
Não existe ação de "rodar código", nem shell, nem comando digitado. O nome
passa pela regex `^[a-z0-9][a-z0-9_-]{0,63}$` (sem `/`, sem `.`, sem `..`). O
arquivo é aberto pelo fd da pasta com `O_NOFOLLOW` (link simbólico é
recusado) e `O_NONBLOCK` (um FIFO não trava nada), conferido pelo `fstat`, e
**executado pelo próprio fd** (`/proc/self/fd/N`). Trocar o arquivo entre a
checagem e a execução não muda o que roda. Argumentos vão direto pro `execve`,
sem shell. A interface e a CLI só mostram o que o agente listou.
Testes: `test_inv1_*`.

**2. Metadados só com `ast`.**
O arquivo é lido como texto e passado pelo `ast.parse`. Só contam atribuições
simples no topo do módulo, com valores literais (`ast.literal_eval`). Um
`PARAMETROS` que não é literal bloqueia o comando (sem o esquema não dá pra
validar). Testes: `test_inv2_listing_never_executes`,
`test_metadata_never_executes_code`.

**3. Todo pedido é autenticado.**
HMAC-SHA256 com uma chave derivada do segredo da direção e dos dois nomes.
Pra um nome desconhecido o agente faz a mesma conta com uma chave qualquer,
então o tempo de resposta não diz quais nomes existem.
Pedido velho (mais de 60 s de diferença), nonce repetido (memória de 180 s) e
`ts` que não é número são recusados. O JSON é estrito (sem `NaN`, sem chave
repetida). Pra quem não se autenticou, o erro **não é assinado**, não tem
nonce, e é o mesmo pra "controlador desconhecido" e "assinatura errada".
Testes: `test_inv3_*`.

**4. Respostas autenticadas, amarradas ao pedido e em ordem.**
Cada frame de resposta é assinado com o rótulo de resposta (um pedido
assinado não vale como resposta), leva o nonce do pedido, `de`, `para` e um
`seq` que começa em 0 e sobe de 1 em 1. O controlador para no primeiro frame
errado, pulado, repetido, fora de ordem, de outro pedido, de outra direção,
ou depois do fim. Conexão que cai no meio é erro. Testes: `test_inv4_*`.

**5. Só o IP do Tailscale e só IPs permitidos.**
O agente escuta no IP do Tailscale (nunca em `0.0.0.0` ou `::`, o config
recusa). A lista de redes permitidas é conferida no `verify_request`, logo
depois do `accept`, **antes de ler qualquer byte**. Testes: `test_inv5_*`.

**6. Nunca como root.**
O agente, o servidor e o instalador se recusam a rodar como root. O serviço é
de usuário (`systemctl --user`). Testes: `test_inv6_refuses_root`,
`test_agent_process_refuses_root`, `test_install_refuses_root`.

**7. Dono e permissões.**
Pasta de config, pasta de comandos e arquivo de comando: do seu usuário e sem
escrita pra grupo/outros. Arquivo com segredo (`ponte.json`, `config.json`
do v1): modo 600 e não pode ser link. Se o `ponte.json` ficar inseguro ou
quebrado com o agente rodando, ele **recusa tudo** até alguém consertar
(falha fechada). Auditoria e histórico são 600 em pastas 700.
Testes: `test_inv7_*`, `test_config_requires_mode_600`.

**8. Argumentos e parâmetros.**
Argumento solto só chega em comando com `ACEITA_ARGS = True` (até 32, até
4096 caracteres, sem byte nulo). Comando com `PARAMETROS` só recebe valores
validados pelo agente contra o esquema (tipo, obrigatório, mínimo, máximo,
regex, opções), e eles chegam **pelo stdin** (não aparecem no `ps`).
Parâmetro desconhecido, tipo errado, caractere de controle num texto de uma
linha: recusado. Testes: `test_inv8_*`, `tests/unit/test_params.py`.

**9. Timeout e grupo de processos.**
Cada comando é líder de uma sessão nova. No timeout, no cancelamento **e
quando o comando termina**, o grupo inteiro recebe SIGTERM e, 3 s depois,
SIGKILL. O líder só é colhido depois (com `WNOWAIT`), então o número do grupo
não é reusado no meio. Se o controlador desconectar, o job segue com o
timeout dele e o resultado fica no histórico. Parar o agente cancela todos os
jobs, e o systemd (`KillMode=control-group`) garante o resto.
Testes: `test_inv9_*`, `tests/unit/test_runner.py`.

**10. Limites de tamanho.**
Pedido até 64 KiB, resposta até 1 MiB por frame, o tamanho é conferido antes
de ler. Saída guardada até 256 KiB por fluxo (o resto é lido e descartado, o
comando nunca trava). Arquivo de comando até 1 MiB. Auditoria gira em 1 MiB
e guarda 5 arquivos. Histórico da interface guarda 1000 execuções.
Testes: `test_inv10_*`.

**11. Um pedido nunca derruba o agente.**
Todo pedido roda numa thread com `try` em volta. Erro depois da autenticação
vira um erro assinado ("erro interno"). Lixo, JSON inválido, conexões lentas
e conexões demais não param o agente. A linha mágica e o pedido têm 15 s **no
total** pra chegar (quem manda um byte de vez em quando é cortado), cada IP
tem no máximo 4 conexões abertas, e a regex de um `PARAMETROS` roda num
processo à parte com limite de 1 s (o `re` do Python segura o GIL, e uma
regex que trava congelaria o agente inteiro). Texto vindo de fora é cortado e
tem os caracteres de controle escapados antes de ir pro log, então ninguém
forja uma linha no journal. Recusas de quem não se autenticou vão pra
auditoria no máximo 30 por minuto (o resto vira uma contagem), pra ninguém
apagar o histórico da auditoria mandando lixo.
Testes: `test_inv11_*`, `test_review_*`.

## Pareamento

**Fluxo.** Na máquina que vai executar (o agente), `ponte parear` (ou
"Deixar outra controlar" na interface) abre uma janela de **2 minutos** na
porta 47801 e mostra um código de **8 números**. Na máquina que vai mandar,
você digita esse código. As duas saem com um segredo novo de 32 bytes, igual
dos dois lados, só pra essa direção.

**Por que dá pra usar um código curto.** O código não é usado como senha
direto. As duas pontas rodam **SPAKE2**, um protocolo de troca de chave com
senha (PAKE), sobre o grupo de 2048 bits da RFC 3526. Isso dá três coisas:

1. **Quem só escuta** a conversa não aprende nada que deixe testar códigos
   depois (nem offline). E o segredo final nunca passa pela rede.
2. **Quem finge ser uma das pontas** só consegue testar **um** código por
   tentativa. O agente fecha a janela depois de **3 tentativas erradas**. A
   chance de acertar por sorte é 3 em 100 milhões.
3. O nome das duas máquinas entra na conta. Trocar um nome no meio do
   caminho faz a confirmação falhar.

A ordem das mensagens também importa. O agente só grava o controlador depois
de conferir a prova do controlador. O controlador só manda a prova dele
depois de conferir a do agente. Então quem não sabe o código não ganha nada
pra testar.

**Camadas extras.** O pareamento só aceita IPs da lista de permitidos. Se o
`tailscale` existir, ele confere com `tailscale whois` que o IP é do nó com o
nome que o controlador disse ter. Só vale o nome que o servidor de controle
do Tailscale deu ao nó (`Node.Name`), dentro do **seu** tailnet. O nome que o
próprio aparelho diz ter (`Hostinfo.Hostname`) não conta, e um nó
compartilhado de outro tailnet com o mesmo nome também não passa. Uma máquina não pode parear com o próprio
nome. Re-parear mantém as permissões de comandos que já existiam.

**Ameaças que sobram no pareamento.**
- Alguém do tailnet (ou outro usuário local) pode gastar as 3 tentativas e
  fechar a janela. Isso só atrasa: você abre de novo e ganha um código novo.
- Quem estiver olhando a sua tela vê o código. Ele vale por 2 minutos e uma
  vez só.
- O SPAKE2 foi feito à mão, só com a biblioteca padrão do Python, e não foi
  auditado. Ele não é tempo constante (o `pow` do Python não é). Pra um
  pareamento que acontece de vez em quando, dentro do WireGuard do Tailscale,
  isso é aceitável. Os testes conferem o caminho feliz, o código errado,
  elementos inválidos (0, 1, p−1, fora do subgrupo), nomes trocados e
  mensagem final adulterada.

**Caminho manual.** Continua existindo: `ponte agente autorizar <nome>` na
máquina que executa mostra um segredo, e `ponte maquina adicionar <nome>
--segredo -` na que manda lê esse segredo. O segredo aparece na tela e passa
pela sua área de transferência, então prefira o pareamento.

**Nada de segredo no argv.** Segredo e código de pareamento só entram pelo
terminal (sem eco) ou pelo stdin. A CLI recusa `--segredo <hex>`: os
argumentos de qualquer processo aparecem pra todos os usuários da máquina
(`/proc/<pid>/cmdline`), e o outro usuário local poderia copiar o segredo ou
usar o código antes de você.

## Quem pegou o controlador

Com o `ponte.json` do controlador, a pessoa tem o segredo da direção
controlador → agente. Ela consegue:

- rodar os comandos que esse controlador pode rodar (por padrão todos; dá pra
  limitar em Configurações → Esta máquina → permissões, ou no `ponte.json`
  com `"comandos": ["status"]`);
- com os parâmetros que eles aceitam, validados pelo agente;
- ver o histórico e a saída dos jobs **desse** controlador (não dos outros).

Ela **não** consegue: rodar outra coisa, mudar comandos, ler a pasta de
comandos, ler o config do agente, parear outra máquina sem o código, nem
controlar a outra direção (que tem outro segredo).

O que fazer: na máquina que executa, revogue o controlador
(`ponte agente revogar <nome>` ou o botão de lixeira na interface). Vale na
hora, sem reiniciar nada.

Atenção com config migrado do v1: o v1 usava **um** segredo pras duas
direções. A migração mantém isso pra nada parar de funcionar, e a chave
derivada impede reflexão de pedidos entre direções. Mas quem pegar uma das
máquinas tem o segredo das duas direções. Depois de migrar, **pareie de novo
nas duas direções**.

## Outro usuário local

- Não lê config, comandos, auditoria nem histórico (modos 600/700).
- Não escreve na pasta de comandos (escrita aberta bloqueia tudo).
- Pode conectar na porta do agente pelo IP do Tailscale, mas sem segredo
  recebe só o erro sem assinatura.
- Se ele estiver na **máquina controladora**, sai pelo mesmo IP que você. Aí
  ele pode ocupar as 4 vagas desse IP e atrasar os seus pedidos. Isso é
  negação de serviço, não acesso.
- Vê no `ps` os **argumentos** de comandos antigos (`ACEITA_ARGS`). Por isso
  o `PARAMETROS` manda os valores pelo stdin. Prefira `PARAMETROS` pra
  qualquer coisa sensível, e marque com `"segredo": True` (a auditoria guarda
  `***`, a interface não preenche de novo).

## O serviço

O unit de usuário usa só opções que funcionam sem privilégio e não quebram
comandos de desktop: `NoNewPrivileges` (sudo e pkexec não funcionam dentro de
um comando, de propósito), `RestrictSUIDSGID`, `RestrictNamespaces`,
`LockPersonality`, `RestrictRealtime`, `KeyringMode=private`, filtro de
syscalls e de famílias de socket, `UMask=0077`. O porquê de cada escolha e do
que ficou de fora está no `DECISIONS.md` (D30, D31).

Isso é defesa em profundidade, não uma caixa fechada. Um comando aprovado
pode fazer tudo que o seu usuário faz (é pra isso que ele existe). Por
exemplo, `soltar()` usa o `systemd-run --user` pra abrir um programa fora das
regras do agente.

## O que fica de fora

- **Root** em qualquer das máquinas.
- **Comandos mal escritos.** Um comando aprovado que faz algo perigoso com os
  parâmetros (montar uma linha de shell com eles, por exemplo) é problema do
  comando. Os exemplos mostram como fazer direito.
- **Regex lenta** num `PARAMETROS`. Ela roda num processo à parte com
  limite de 1 s, então não trava o agente, mas o pedido falha ("demorou
  demais"). Use regex simples.
- **Processo que sai do grupo de propósito.** O timeout mata o grupo de
  processos do comando. Um programa que chama `setsid` ou vira daemon sai do
  grupo e não é morto no timeout (é assim que o `soltar()` funciona). Como só
  roda código que você aprovou, isso é escolha do comando. Um cgroup por job
  (`systemd-run --scope`) fecharia esse caso; ficou como melhoria futura.
- **`CONFIRMAR` não é uma barreira.** O agente exige `"confirmado": true`
  pra esses comandos, o que obriga todo controlador (CLI, interface, scripts)
  a perguntar antes. Mas quem pegou o controlador pode mandar `true`. Pra
  impedir de verdade, tire o comando das permissões desse controlador.
- **Negação de serviço** por vários aparelhos do tailnet juntos: com 8 IPs
  diferentes dá pra ocupar as 32 vagas. O agente continua de pé e volta
  quando elas fecham (cada uma dura no máximo 15 s sem mandar o pedido).
- **Relógio.** Se o relógio de uma máquina andar mais de 60 s, os pedidos são
  recusados ("relógios fora de sincronia"). Deixe o NTP ligado
  (`timedatectl`).
