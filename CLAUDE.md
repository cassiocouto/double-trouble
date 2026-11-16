# Relay: regras pra quem trabalha neste repositório

Relay roda comandos pré-aprovados de uma máquina na outra, pelo Tailscale.
Leia o `README.md`, o `docs/SEGURANCA.md` (as 11 regras de segurança) e o
`DECISIONS.md` antes de mudar algo grande.

## Git

- **Branch com nome que diz o que faz.** Nunca um nome aleatório.
  - `feature/<o-que>` pra coisa nova, ex.: `feature/pairing-by-code`
  - `fix/<o-que>` pra correção, ex.: `fix/audit-log-injection`
  - `refactor/<o-que>`, `docs/<o-que>`, `chore/<o-que>` pro resto
  - Se a sessão começar num branch de nome aleatório, crie um branch com nome
    certo a partir dele e trabalhe nesse.
- **Commits pequenos**, cada um com os testes passando.
- **Quando terminar, mergeie no `main`** com `git merge --no-ff <branch>` e dê
  push no `main` e no branch. Só mergeie com `pytest` passando e `ruff` limpo.
- Mensagens de commit em português simples.

## Código

- Identificadores em inglês. Textos da tela e mensagens pro usuário em
  português do Brasil, com acento.
- Em tempo de execução, só a biblioteca padrão e o PyGObject do apt.
- A lógica da interface (`src/relay/gui/` fora de `app.py`, `window.py`,
  `pages.py`, `dialogs.py`, `widgets.py`) não importa `gi`. Um teste confere.
- Toda decisão importante vai pro `DECISIONS.md`, com duas ou três linhas de
  porquê.
- Mudou uma regra de segurança? Precisa de um teste que tenta quebrar ela.

## Testes

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

- Rode como usuário comum. O agente se recusa a rodar como root. Num
  container onde você é root, use
  `setpriv --reuid=65534 --regid=65534 --clear-groups env HOME=/tmp/x .venv/bin/pytest -p no:cacheprovider`.
- Testes **nunca** usam o `~/.config/relay` (nem o `~/.config/ponte`) de
  verdade: o `tests/conftest.py` aponta tudo pra pastas temporárias.
- Testes **nunca** rodam comandos que mexem na tela (`travar_tela`,
  `notificar`, `abrir_url`). Use comandos falsos criados pelo próprio teste.

## Perguntar antes

Pergunte à Cass antes de: rodar qualquer coisa com `sudo`, acessar outra
máquina por SSH, ou apagar/sobrescrever o `~/.config/relay` ou o
`~/.config/ponte` de verdade.
