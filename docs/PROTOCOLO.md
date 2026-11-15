# Protocolo do ponte, versão 2

Este é o protocolo entre o **controlador** (CLI ou interface, o lado que manda)
e o **agente** (o lado que executa). O código está em `src/ponte/core/protocol.py`
(formato), `src/ponte/core/client.py` (controlador) e `src/ponte/agent/server.py`
(agente).

O v2 não conversa com o v1. Versões diferentes param logo no começo, com um
erro claro dos dois lados.

## 1. Transporte

- TCP, por padrão na porta **47800**, só no IP do Tailscale do agente.
- Uma conexão leva **um pedido**. O agente pode mandar **várias respostas**
  nela (saída ao vivo). Depois da última, o agente fecha.
- Pra cancelar um job, o controlador abre outra conexão com `cancelar`.

## 2. Linha mágica (versão)

1. O controlador manda os 8 bytes `PONTE/2\n`.
2. O agente responde os mesmos 8 bytes.

Casos de versão diferente:

| O agente recebe        | O agente faz                                      | O controlador mostra                    |
|------------------------|---------------------------------------------------|-----------------------------------------|
| `{` (cliente v1)       | manda uma linha JSON v1 com o erro e fecha        | o ponte.py v1 mostra o erro             |
| `PONTE/<outra>\n`      | manda `PONTE/2\n` e fecha                         | "fala outra versão do protocolo"        |
| qualquer outra coisa   | fecha sem mandar nada                             | —                                       |
| (agente é o v1)        | o v1 não entende e fecha                          | "pode ser o ponte.py antigo (v1)"       |

## 3. Frames

Depois da linha mágica, tudo é frame:

```
+----------------------+-----------------------------+
| tamanho: 4 bytes     | corpo: JSON em UTF-8        |
| inteiro big-endian   | (um objeto)                 |
+----------------------+-----------------------------+
```

- Pedido: no máximo **64 KiB**. Resposta: no máximo **1 MiB** por frame.
- O tamanho é conferido **antes** de ler o corpo. Tamanho 0 é erro.
- O JSON é estrito: sem `NaN`, sem `Infinity`, sem chave repetida.

## 4. Chaves

Cada direção tem seu segredo de 32 bytes (ou mais). Pra direção
controlador `C` → agente `A`, a chave de assinatura é

```
K = HMAC-SHA256(segredo, JSON(["ponte/2", C, A]))
```

`JSON(...)` é `json.dumps` com `ensure_ascii=True`. A lista tira qualquer
ambiguidade entre nomes (`"ab","c"` ≠ `"a","bc"`).

Por que derivar? Mesmo que alguém use o mesmo segredo nas duas direções (o
config migrado do v1 faz isso), `K(C→A)` ≠ `K(A→C)`. Um pedido de uma direção
não vale na outra.

## 5. Assinatura

```
mac = hex(HMAC-SHA256(K, rotulo + canonico(mensagem sem "mac")))
```

- `rotulo` é `ponte/2 pedido\n` pra pedidos e `ponte/2 resposta\n` pra
  respostas. Uma mensagem assinada como pedido não vale como resposta.
- `canonico` é `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
- `mac` tem exatamente 64 caracteres hexadecimais minúsculos. A comparação é
  em tempo constante, sobre bytes ASCII.

## 6. Pedido

```json
{
  "v": 2,
  "de": "cass-dell-brasil",
  "para": "cass-dell-g15-5511",
  "ts": 1763246400,
  "nonce": "32 caracteres hex",
  "acao": "rodar",
  "...": "campos da ação",
  "mac": "64 caracteres hex"
}
```

O agente confere, nesta ordem:

1. IP de origem na lista `permitidos` (antes de ler qualquer byte).
2. Linha mágica.
3. Frame de até 64 KiB, JSON estrito.
4. `de` é um controlador conhecido **e** o `mac` confere com a chave dele.
   Se falhar, responde o **erro sem assinatura** (seção 8) e fecha.
5. `v == 2`, `para` é o nome deste agente.
6. `ts` é número (não booleano) e está a no máximo **60 s** do relógio do agente.
7. `nonce` tem 32 hex e nunca foi visto nos últimos **180 s**.
8. Se ligado, `tailscale whois` do IP bate com o nó esperado do controlador.

Do passo 5 em diante, todo erro é **assinado**.

## 7. Respostas

Todo frame de resposta tem

```json
{
  "v": 2,
  "de": "<agente>",
  "para": "<controlador>",
  "nonce": "<nonce do pedido>",
  "seq": 0,
  "tipo": "...",
  "fim": false,
  "mac": "..."
}
```

- `seq` começa em 0 e sobe de 1 em 1.
- O último frame tem `fim: true`. Depois dele o agente fecha.

O controlador recusa (e para) se um frame:

- não tem `mac`, ou o `mac` não confere com `K(C→A)` e o rótulo de resposta;
- tem `v`, `de`, `para` ou `nonce` diferente do esperado;
- tem `seq` fora de ordem (pulado, repetido, não inteiro);
- chega depois do `fim`.

Se a conexão cai antes do `fim`, é erro ("caiu no meio da resposta").

## 8. Erros

Erro assinado (quem pediu se autenticou):

```json
{"...": "campos da seção 7", "tipo": "erro", "fim": true, "erro": "mensagem", "codigo_erro": "relogio"}
```

Erro sem assinatura (quem pediu **não** se autenticou):

```json
{"v": 2, "tipo": "erro", "fim": true, "autenticado": false, "erro": "assinatura inválida ou máquina desconhecida"}
```

Não tem `mac` nem `nonce`. Controlador desconhecido e assinatura errada dão a
**mesma** mensagem, pra ninguém descobrir nomes de controladores.

Códigos de erro: `versao`, `destino`, `relogio`, `nonce`, `repetido`,
`whois`, `acao`, `nome`, `permissao`, `args`, `parametro`, `comando`,
`ocupado`, `job`, `pedido`, `grande`, `interno`.

## 9. Ações

### `ping`

Pedido: nada além do cabeçalho. Resposta: um frame `pong`.

```json
{"tipo": "pong", "fim": true, "hora": 1763246400.12, "nome": "cass-dell-g15-5511",
 "versao": "2.0.0", "protocolo": 2, "jobs_rodando": 0, "ligado_desde": 1763240000.0}
```

O controlador calcula a latência e a diferença de relógio
(`hora - (envio + latência / 2)`).

### `listar`

Resposta: um frame `comandos` com a lista. Só aparecem os comandos que esse
controlador pode rodar. Cada item:

```json
{"nome": "notificar", "descricao": "Mostra uma notificação...", "detalhes": "...",
 "aceita_args": false, "timeout": 15, "confirmar": false,
 "icone": "preferences-system-notifications-symbolic", "categoria": "Tela",
 "parametros": [{"nome": "texto", "tipo": "texto", "rotulo": "Mensagem",
                 "obrigatorio": true, "min": 1, "max": 500}],
 "avisos": ["..."]}
```

Comando bloqueado vem como `{"nome": "x", "bloqueado": "motivo"}`.

### `rodar`

Pedido:

```json
{"acao": "rodar", "comando": "notificar", "parametros": {"texto": "oi"}, "args": [], "seguir": true}
```

- `parametros` só pra comandos com `PARAMETROS`. `args` só pra comandos com
  `ACEITA_ARGS = True` e sem `PARAMETROS`. Os dois juntos é erro.
- `seguir` (padrão `true`): manda a saída ao vivo. Com `false`, o agente só
  confirma o início e fecha. O job continua.

Respostas, em ordem:

1. `aceito` com `job` (id), `comando`, `timeout`. Com `seguir: false` este é o fim.
2. zero ou mais `saida` com `job`, `fluxo` (`stdout` ou `stderr`) e `dados` (texto).
3. `vivo` a cada 10 s de silêncio, pra o controlador saber que a conexão está viva.
4. `fim` com `resultado`:

```json
{"job": "a1b2c3d4e5f60718", "comando": "notificar", "estado": "ok", "inicio": 1763246400.0,
 "duracao": 0.12, "codigo": 0, "truncado": false, "timeout": 15,
 "ok": true, "estourou_tempo": false, "cancelado": false}
```

`estado` é `rodando`, `ok`, `falhou`, `cancelado`, `tempo_esgotado` ou `erro`.

Se o controlador desconectar, o job **continua** até acabar ou estourar o
tempo. O resultado fica no histórico.

### `cancelar`

Pedido: `{"acao": "cancelar", "job": "<id>"}`. Resposta: `cancelado` com `job` e
`estado`. O agente manda SIGTERM pro grupo e SIGKILL 3 s depois.

### `status`

Pedido: `{"acao": "status", "job": "<id>", "seguir": false}`.

Respostas: `job` (com `estado` = o mesmo objeto do `resultado`), depois a saída
guardada em frames `saida`, depois `fim`. Com `seguir: true` e o job ainda
rodando, a saída continua ao vivo até o fim.

### `historico`

Pedido: `{"acao": "historico", "limite": 20}` (1 a 50). Resposta: `historico`
com `jobs`, uma lista de resumos (sem saída), do mais novo pro mais velho.

Cada controlador só enxerga os próprios jobs. Job de outro controlador é
tratado como inexistente em `status` e `cancelar`.

## 10. Como o comando recebe os parâmetros

Os valores validados vão como **um objeto JSON no stdin** do comando, já com
os padrões preenchidos. Parâmetro opcional sem valor e sem padrão chega como
`null` (ou `false`, se for booleano). Exemplo:

```json
{"texto": "oi", "urgente": false}
```

Por que stdin? Argumentos de linha de comando aparecem no `ps` pra qualquer
usuário da máquina. Variável de ambiente vaza pra todo processo filho. O
stdin é privado do comando. O helper `ponte.comando.parametros()` lê isso.

Comandos antigos com `ACEITA_ARGS = True` continuam recebendo `args` no
`sys.argv`, como no v1.

## 11. Limites

| O quê                                  | Limite                    |
|----------------------------------------|---------------------------|
| frame de pedido                        | 64 KiB                    |
| frame de resposta                      | 1 MiB                     |
| texto por frame `saida`                | 32 Ki caracteres          |
| saída guardada por fluxo de um job     | 256 KiB (o resto é lido e descartado) |
| argumentos                             | 32, até 4096 caracteres, sem byte nulo |
| parâmetros                             | 32 por comando, texto até 4096 |
| diferença de relógio                   | 60 s                      |
| memória de nonces                      | 180 s, até 100 mil        |
| conexões abertas no agente             | 32                        |
| jobs em paralelo                       | 4 (configurável, `max_paralelo`) |
| jobs guardados no histórico            | 50                        |
| tempo pra mandar o pedido              | 15 s                      |
