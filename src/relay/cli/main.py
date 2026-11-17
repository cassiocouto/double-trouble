"""CLI do relay. Serve pra scripts, pra depurar e pra configurar sem a interface.

Códigos de saída
  0       deu certo (ou o código do próprio comando remoto)
  1       a outra máquina recusou ou o comando falhou ao iniciar
  2       uso errado ou config com problema
  3       não deu pra falar com a outra máquina
  4       assinatura, versão ou ordem de mensagens com problema
  130     cancelado com Ctrl+C
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import queue
import secrets
import sys
import threading
import time
from importlib import resources

from .. import __version__
from ..core import commands, paths
from ..core import config as config_mod
from ..core.client import Client
from ..core.errors import (
    AuthError,
    CommandError,
    ConfigError,
    ConnectionFailed,
    ParamError,
    ProtocolError,
    RelayError,
    RemoteError,
)
from ..core.params import parse_cli_value
from ..core.safety import write_private


def say(msg: str = "") -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"relay: {msg}", file=sys.stderr, flush=True)


class Exit(Exception):
    def __init__(self, code: int, message: str = ""):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# config local


def load_cfg() -> config_mod.Config:
    path = paths.config_file()
    if not path.exists():
        hint = "rode 'relay init'"
        raise ConfigError(f"não achei {path}, {hint}")
    return config_mod.load(path)


ARGV_WARNING = (
    "por segurança, segredo e código não vão na linha de comando (outros usuários da máquina "
    "veem os argumentos no ps). Use --segredo - e cole quando pedir, ou mande pelo stdin"
)


def read_secret(value: str | None, prompt: str) -> str:
    """Lê segredo ou código do terminal (sem eco) ou do stdin. Nunca do argv."""
    if value not in (None, "-"):
        raise Exit(2, ARGV_WARNING)
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.readline().strip()


def write_backup(source, target) -> None:
    """Cópia com modo 600 desde o primeiro byte (o copy2 deixaria uma janela aberta)."""
    write_private(target, source.read_bytes(), mode=0o600)


def install_examples(force: bool = False) -> list[str]:
    folder = paths.commands_dir()
    paths.ensure_private_dir(folder)
    created = []
    pkg = resources.files("relay") / "data" / "exemplos"
    for item in sorted(pkg.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".py"):
            continue
        target = folder / item.name
        if target.exists() and not force:
            continue
        if target.exists():
            backup = target.with_name(f"{item.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            write_backup(target, backup)
        write_private(target, item.read_text(encoding="utf-8"), mode=0o600)
        created.append(item.name[:-3])
    return created


def cmd_init(a) -> None:
    path = paths.config_file()
    if path.exists() and not a.forcar:
        raise Exit(2, f"{path} já existe. Use --forcar pra recriar (o atual vira backup)")
    base = paths.base_dir()
    paths.ensure_private_dir(base)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        write_backup(path, backup)
        say(f"backup do config anterior em {backup}")
    cfg = config_mod.default_config(a.nome)
    if a.escutar:
        cfg.agent.listen = a.escutar
    cfg.agent.port = a.porta
    cfg.agent.pairing_port = a.porta + 1
    config_mod.Config.from_json(cfg.to_json())  # valida o que veio da linha de comando
    config_mod.save(cfg, path)
    created = [] if a.sem_exemplos else install_examples()
    say(f"pronto, config em {path}")
    say(f"  esta máquina  {cfg.name}, escutando em {cfg.agent.listen}:{cfg.agent.port}")
    if created:
        say(f"  exemplos      {', '.join(created)} em {paths.commands_dir()}")
    say("\nPróximo passo: parear com a outra máquina (uma vez só, vale nas duas direções)")
    say("  numa máquina     relay parear")
    say("  na outra         relay parear <nome>   (pode ser só um pedaço do nome, como g15)")


# --------------------------------------------------------------------------
# ações remotas


def client_for(query: str, **kw) -> Client:
    return Client.for_machine(load_cfg(), query, **kw)


def cmd_ping(a) -> None:
    c = client_for(a.maquina, read_timeout=15)
    p = c.ping()
    sign = "+" if p.clock_skew >= 0 else ""
    jobs = f", {p.running} job(s) rodando" if p.running else ""
    say(
        f"{p.machine} respondeu em {p.latency_ms:.0f} ms (relógio {sign}{p.clock_skew:.1f} s, agente {p.version}{jobs})"
    )
    if abs(p.clock_skew) > 30:
        warn("os relógios estão bem diferentes. Acima de 60 s os pedidos são recusados")


def _extras(c: dict) -> list[str]:
    extras = []
    if c.get("parametros"):
        extras.append("parâmetros: " + ", ".join(p["nome"] for p in c["parametros"] if isinstance(p, dict)))
    if c.get("aceita_args"):
        extras.append("aceita argumentos")
    if c.get("confirmar"):
        extras.append("pede confirmação")
    if c.get("timeout") not in (None, commands.DEFAULT_TIMEOUT):
        extras.append(f"limite {c['timeout']:g} s")
    return extras


def print_command_list(title: str, items: list[dict]) -> None:
    if not items:
        say(f"{title} não tem nenhum comando ainda")
        return
    say(f"{title} tem {len(items)} comando(s)")
    groups: dict[str, list[dict]] = {}
    for c in items:
        groups.setdefault(str(c.get("categoria") or "Outros"), []).append(c)
    width = max(len(c["nome"]) for c in items)
    for cat in sorted(groups, key=lambda k: (k == "Outros", k.lower())):
        say(f"\n  {cat}")
        for c in groups[cat]:
            if "bloqueado" in c:
                say(f"    {c['nome']:<{width}}  BLOQUEADO, {c['bloqueado']}")
                continue
            tail = f"  [{'; '.join(_extras(c))}]" if _extras(c) else ""
            say(f"    {c['nome']:<{width}}  {c.get('descricao') or '(sem descrição)'}{tail}")
            for w in c.get("avisos") or []:
                say(f"    {'':<{width}}  aviso: {w}")


def cmd_listar(a) -> None:
    c = client_for(a.maquina, read_timeout=15)
    print_command_list(c.machine.name, c.list_commands())


def cmd_comandos(a) -> None:
    """Lista os comandos DESTA máquina lendo a pasta direto, sem rede."""
    print_command_list(f"esta máquina ({paths.commands_dir()})", commands.list_commands())


def build_params(meta: dict, pairs: list[str], positional: list[str]) -> dict:
    specs = [p for p in meta.get("parametros") or [] if isinstance(p, dict)]
    by_name = {p["nome"]: p for p in specs}
    values: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise Exit(2, f"-p precisa ser nome=valor, veio '{pair}'")
        if key not in by_name:
            names = ", ".join(by_name) or "nenhum"
            raise Exit(2, f"'{meta['nome']}' não tem o parâmetro '{key}'. Tem: {names}")
        values[key] = parse_cli_value(by_name[key], raw)
    # Argumentos soltos preenchem, em ordem, os parâmetros que faltam.
    free = [p for p in specs if p["nome"] not in values]
    if len(positional) > len(free):
        raise Exit(2, f"argumentos demais pra '{meta['nome']}'. Use -p nome=valor")
    for spec, raw in zip(free, positional, strict=False):
        values[spec["nome"]] = parse_cli_value(spec, raw)
    return values


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise Exit(2, "este comando pede confirmação. Rode com --sim pra confirmar sem perguntar")
    answer = input(f"{question} [s/N] ").strip().lower()
    return answer in ("s", "sim", "y", "yes")


def cmd_rodar(a) -> None:
    c = client_for(a.maquina)
    listing = {item["nome"]: item for item in c.list_commands()}
    meta = listing.get(a.comando)
    if meta is None:
        raise Exit(1, f"{c.machine.name} não tem o comando '{a.comando}' (ou não deixa você rodar)")
    if "bloqueado" in meta:
        raise Exit(1, f"'{a.comando}' está bloqueado em {c.machine.name}: {meta['bloqueado']}")
    params = None
    args = list(a.args)
    if meta.get("parametros") is not None:
        params = build_params(meta, a.param, args)
        args = []
    elif a.param:
        raise Exit(2, f"'{a.comando}' não tem PARAMETROS, passe argumentos soltos")
    confirmed = bool(meta.get("confirmar"))
    if confirmed and not confirm(f"Rodar '{a.comando}' em {c.machine.name}?", a.sim):
        raise Exit(1, "cancelado, nada foi rodado")
    stream_run(
        c,
        a,
        lambda client, on_event: client.run(a.comando, params, args, on_event, not a.sem_esperar, confirmed),
    )


def stream_run(c: Client, a, start) -> None:
    """Roda numa thread e imprime ao vivo. Ctrl+C cancela o job; o segundo sai na hora."""
    events: queue.Queue = queue.Queue()
    box: dict = {}

    def work() -> None:
        try:
            box["outcome"] = start(c, events.put)
        except BaseException as e:
            box["error"] = e
        finally:
            events.put(None)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    job_id = None
    result = None
    interrupted = 0
    while True:
        try:
            ev = events.get(timeout=0.2)
        except queue.Empty:
            continue
        except KeyboardInterrupt:
            interrupted += 1
            if interrupted > 1 or not job_id:
                c.abort()
                raise Exit(130, "interrompido. O job pode continuar rodando lá") from None
            warn(f"cancelando o job {job_id}... (Ctrl+C de novo pra sair sem esperar)")
            try:
                Client(c.cfg, c.machine).cancel(job_id)
            except RelayError as e:
                warn(f"não consegui cancelar ({e})")
            continue
        if ev is None:
            break
        tipo = ev.get("tipo")
        if tipo == "aceito":
            job_id = ev.get("job")
            if a.sem_esperar:
                say(f"job {job_id} começou em {c.machine.name}. Veja com: relay status {a.maquina} {job_id}")
        elif tipo == "saida":
            target = sys.stderr if ev.get("fluxo") == "stderr" else sys.stdout
            target.write(ev.get("dados", ""))
            target.flush()
        elif tipo in ("fim", "job"):
            result = ev.get("resultado") or ev.get("estado")
    t.join()
    if "error" in box:
        raise box["error"]
    if a.sem_esperar and result is None:
        return
    finish_result(result)


def finish_result(result: dict | None) -> None:
    if not isinstance(result, dict):
        raise Exit(1, "o job terminou sem resultado")
    estado = result.get("estado")
    if result.get("truncado"):
        warn("a saída foi cortada em 256 KiB por fluxo")
    if estado == "tempo_esgotado":
        warn(f"passou do limite de {result.get('timeout')} s e foi encerrado")
    elif estado == "cancelado":
        warn("job cancelado")
        raise Exit(130)
    elif estado == "erro":
        raise Exit(1, str(result.get("erro") or "o comando não iniciou"))
    elif estado == "rodando":
        return
    code = result.get("codigo")
    if not isinstance(code, int):
        raise Exit(1)
    if code != 0:
        raise Exit(128 - code if code < 0 else min(code, 255))


def cmd_status(a) -> None:
    c = client_for(a.maquina)
    stream_run(c, a, lambda client, on_event: client.status(a.job, a.seguir, on_event))


def cmd_cancelar(a) -> None:
    frame = client_for(a.maquina, read_timeout=15).cancel(a.job)
    say(f"cancelamento pedido (estado agora: {frame.get('estado')})")


def cmd_historico(a) -> None:
    c = client_for(a.maquina, read_timeout=15)
    jobs = c.history(a.limite)
    if not jobs:
        say(f"nenhum job seu em {c.machine.name} desde que o agente ligou")
        return
    say(f"{'quando':<19}  {'job':<16}  {'comando':<20}  {'estado':<14}  código  duração")
    for j in jobs:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(j.get("inicio", 0)))
        code = j.get("codigo")
        code_s = "" if code is None else str(code)
        say(
            f"{when:<19}  {j.get('job', ''):<16}  {str(j.get('comando'))[:20]:<20}  "
            f"{j.get('estado', ''):<14}  {code_s:>6}  {j.get('duracao', 0):.1f} s"
        )


# --------------------------------------------------------------------------
# configuração


def cmd_maquinas(a) -> None:
    cfg = load_cfg()
    say(f"esta máquina: {cfg.name}")
    if not cfg.machines:
        say("não controla nenhuma máquina ainda (relay parear <nome>)")
    for m in cfg.machines.values():
        say(f"  controla  {m.name:<24} {m.address}:{m.port}")
    for ctrl in cfg.agent.controllers.values():
        allowed = "todos os comandos" if ctrl.commands is None else ", ".join(ctrl.commands) or "nenhum comando"
        say(f"  aceita    {ctrl.name:<24} {allowed}")


def cmd_maquina_adicionar(a) -> None:
    name = config_mod.check_machine_name(config_mod.normalize_machine_name(a.nome))
    secret = config_mod.parse_secret(read_secret(a.segredo, f"segredo pra {name}: "), name)

    def change(cfg: config_mod.Config) -> None:
        cfg.machines[name] = config_mod.MachineEntry(name=name, address=a.endereco or name, port=a.porta, secret=secret)

    config_mod.update(change)
    say(f"pronto, esta máquina agora manda comandos pra {name}. Teste com: relay ping {name}")


def cmd_maquina_remover(a) -> None:
    def change(cfg: config_mod.Config) -> None:
        if a.nome not in cfg.machines:
            raise Exit(2, f"não conheço a máquina '{a.nome}'")
        del cfg.machines[a.nome]

    config_mod.update(change)
    say(f"{a.nome} removida")


def cmd_agente_autorizar(a) -> None:
    name = config_mod.check_machine_name(config_mod.normalize_machine_name(a.nome))
    if a.segredo is not None:
        secret = config_mod.parse_secret(read_secret(a.segredo, f"segredo de {name}: "), name)
        generated = False
    else:
        secret = secrets.token_bytes(32)
        generated = True
    allowed = None if a.comandos in (None, "*") else [x for x in a.comandos.split(",") if x]

    def change(cfg: config_mod.Config) -> None:
        cfg.agent.controllers[name] = config_mod.ControllerEntry(
            name=name, secret=secret, commands=allowed, tailscale_node=a.no_tailscale
        )

    cfg = config_mod.update(change)
    say(f"pronto, {name} pode mandar comandos pra esta máquina ({cfg.name})")
    if generated:
        say(f"\nNa {name}, rode o comando abaixo e cole este segredo quando ele pedir:\n")
        say(f"  relay maquina adicionar {cfg.name} --segredo -\n")
        say(f"  segredo: {secret.hex()}\n")
        say("Prefira 'relay parear', que não precisa copiar segredo nenhum.")


def cmd_agente_revogar(a) -> None:
    def change(cfg: config_mod.Config) -> None:
        if a.nome not in cfg.agent.controllers:
            raise Exit(2, f"'{a.nome}' não estava autorizada")
        del cfg.agent.controllers[a.nome]

    config_mod.update(change)
    say(f"{a.nome} não pode mais mandar comandos pra cá (o agente recarrega o config sozinho)")


def cmd_agente_permissoes(a) -> None:
    """Mostra ou muda quais comandos uma máquina pareada pode rodar aqui."""
    cfg = load_cfg()
    name = config_mod.normalize_machine_name(a.nome)
    if name not in cfg.agent.controllers:
        known = ", ".join(cfg.agent.controllers) or "nenhuma"
        raise Exit(2, f"'{name}' não está pareada com esta máquina. Pareadas: {known}")
    if not a.comandos:
        entry = cfg.agent.controllers[name]
        allowed = "todos os comandos" if entry.commands is None else ", ".join(entry.commands) or "nenhum comando"
        say(f"{name} pode rodar aqui: {allowed}")
        return
    if a.comandos == ["*"]:
        chosen = None
    else:
        chosen = []
        for item in a.comandos:
            for cmd in item.split(","):
                cmd = cmd.strip()
                if not cmd:
                    continue
                if not commands.valid_name(cmd):
                    raise Exit(2, f"'{cmd}' não é um nome de comando válido")
                if cmd not in chosen:
                    chosen.append(cmd)
        missing = [c for c in chosen if not (paths.commands_dir() / f"{c}.py").exists()]
        if missing:
            warn(f"aviso: ainda não existe aqui: {', '.join(missing)} (vale quando você criar)")

    def change(cfg: config_mod.Config) -> None:
        cfg.agent.controllers[name].commands = chosen

    config_mod.update(change)
    shown = "todos os comandos" if chosen is None else ", ".join(chosen) or "nenhum comando"
    say(f"pronto, {name} agora pode rodar aqui: {shown} (vale na hora)")


def cmd_config(a) -> None:
    cfg = load_cfg()
    data = cfg.to_json()
    for entry in [*data["agente"]["controladores"].values(), *data["maquinas"].values()]:
        entry["segredo"] = "(escondido)"
    import json

    say(json.dumps(data, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------


def cmd_parear(a) -> None:
    from ..core import pairing, tailscale

    target = a.maquina or a.com
    cfg = load_cfg()
    if target:
        code = read_secret(None, "Código que apareceu na outra máquina: ")
        say(f"pareando com {target}...")
        paired = pairing.pair_and_save(target, code, a.porta)
        say(f"pronto. Esta máquina e {paired.name} agora mandam uma na outra.")
        say(f"teste com:  relay ping {paired.name}")
        return
    host = a.escutar or (cfg.agent.listen if cfg.agent.listen != "tailscale" else tailscale.own_ip(4))
    if not host:
        raise Exit(3, "não achei o IP do Tailscale (tailscale ip -4). O Tailscale está ligado?")
    window = pairing.PairingWindow(cfg, host, port=a.porta, seconds=a.tempo, on_event=lambda msg: say(f"  {msg}"))
    say(f"Pareamento aberto por {a.tempo:.0f} s.\n")
    say(f"    código:  {pairing.format_code(window.code)}\n")
    say("Na outra máquina, rode o comando abaixo e digite o código:\n")
    say(f"    relay parear {cfg.name}\n")
    say("(dá pra usar só um pedaço do nome). Depois as duas mandam uma na outra.")
    say(f"Depois de {pairing.MAX_FAILURES} códigos errados o pareamento fecha.")
    try:
        result = window.serve()
    except KeyboardInterrupt:
        window.stop()
        raise Exit(130, "pareamento cancelado") from None
    if result is None:
        raise Exit(1, "ninguém pareou. Rode 'relay parear' de novo pra um código novo")
    pairing.save_result(result)
    say(f"\npronto. {result.controller} e esta máquina agora mandam uma na outra.")
    say("O agente relê o config sozinho. Pra limitar o que cada uma roda: relay agente permissoes")


def cmd_resumo(a) -> None:
    """O que aparece com `relay` sozinho: quem é esta máquina e com quem ela fala."""
    import concurrent.futures

    path = paths.config_file()
    if not path.exists():
        say("O relay ainda não foi configurado nesta máquina. Rode o instalador: scripts/install.sh")
        return
    cfg = config_mod.load(path)
    say(f"Esta máquina: {cfg.name}\n")
    names = sorted(set(cfg.machines) | set(cfg.agent.controllers))
    if not names:
        say("Ainda não está pareada com nenhuma máquina.")
        say("Numa máquina rode  relay parear  e na outra  relay parear <nome>")
        return

    def check(name: str) -> str:
        if name not in cfg.machines:
            return "não dá pra mandar nela daqui"
        try:
            p = Client(cfg, cfg.machines[name], connect_timeout=3, read_timeout=3).ping()
        except RelayError as e:
            return f"offline ({str(e)[:80]})"
        return f"online, {p.latency_ms:.0f} ms"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        status = dict(zip(names, pool.map(check, names), strict=True))
    say("Pareada com")
    for name in names:
        sends = "esta manda nela" if name in cfg.machines else ""
        receives = "ela manda nesta" if name in cfg.agent.controllers else ""
        both = " e ".join(x for x in (sends, receives) if x)
        say(f"  {name}")
        say(f"      {both}")
        say(f"      {status[name]}")
    first = names[0]
    say("\nComandos mais usados")
    say(f"  relay listar {first}              comandos que ela aceita")
    say(f"  relay rodar {first} status        roda um comando lá, com a saída ao vivo")
    say(f"  relay historico {first}           o que você rodou lá")
    say("  relay parear                      parear com outra máquina")
    say("  relay --help                      todos os comandos")


def cmd_exemplos(a) -> None:
    if a.atualizar and not confirm(
        f"Trocar os exemplos em {paths.commands_dir()} pelas versões novas? (os atuais viram .bak)", a.sim
    ):
        raise Exit(1, "nada foi mudado")
    created = install_examples(force=a.atualizar)
    if created:
        say(f"exemplos escritos em {paths.commands_dir()}: {', '.join(created)}")
    else:
        say("os exemplos já existem. Use --atualizar pra trocar pelas versões novas")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relay", description="Uma máquina roda comandos pré-aprovados na outra.")
    p.add_argument("--versao", action="version", version=f"relay {__version__}")
    sub = p.add_subparsers(dest="acao", metavar="ação")
    p.set_defaults(func=cmd_resumo)

    s = sub.add_parser("init", help="cria o config e os comandos de exemplo")
    s.add_argument("--nome", help="nome desta máquina (padrão: o hostname)")
    s.add_argument("--escutar", help="IP pra escutar (padrão: o IP do Tailscale)")
    s.add_argument("--porta", type=int, default=config_mod.DEFAULT_PORT)
    s.add_argument("--forcar", action="store_true", help="recria o config (o atual vira backup)")
    s.add_argument("--sem-exemplos", action="store_true", help="não cria os comandos de exemplo")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("ping", help="vê se a outra máquina responde")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_ping)

    s = sub.add_parser("listar", help="mostra os comandos que a outra máquina aceita")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_listar)

    s = sub.add_parser("rodar", help="roda um comando na outra máquina, com saída ao vivo")
    s.add_argument("maquina")
    s.add_argument("comando")
    s.add_argument("args", nargs="*", help="argumentos (ou valores dos parâmetros, em ordem)")
    s.add_argument("-p", "--param", action="append", default=[], metavar="NOME=VALOR")
    s.add_argument("--sim", action="store_true", help="confirma sem perguntar")
    s.add_argument("--sem-esperar", action="store_true", help="só inicia o job e mostra o id")
    s.set_defaults(func=cmd_rodar)

    s = sub.add_parser("status", help="mostra um job (e acompanha com --seguir)")
    s.add_argument("maquina")
    s.add_argument("job")
    s.add_argument("--seguir", action="store_true")
    s.set_defaults(func=cmd_status, sem_esperar=False)

    s = sub.add_parser("cancelar", help="cancela um job")
    s.add_argument("maquina")
    s.add_argument("job")
    s.set_defaults(func=cmd_cancelar)

    s = sub.add_parser("historico", help="seus jobs recentes na outra máquina")
    s.add_argument("maquina")
    s.add_argument("--limite", type=int, default=20)
    s.set_defaults(func=cmd_historico)

    s = sub.add_parser("comandos", help="lista os comandos desta máquina e por que algum está bloqueado")
    s.set_defaults(func=cmd_comandos)

    s = sub.add_parser("maquinas", help="mostra quem esta máquina controla e quem pode controlar ela")
    s.set_defaults(func=cmd_maquinas)

    s = sub.add_parser("maquina", help="adiciona ou remove uma máquina controlada (caminho manual)")
    msub = s.add_subparsers(dest="sub", required=True)
    m = msub.add_parser("adicionar")
    m.add_argument("nome")
    m.add_argument("--endereco", help="padrão: o próprio nome (MagicDNS)")
    m.add_argument("--porta", type=int, default=config_mod.DEFAULT_PORT)
    m.add_argument("--segredo", nargs="?", const="-", help="lê o segredo do stdin (ou pergunta, sem mostrar)")
    m.set_defaults(func=cmd_maquina_adicionar)
    m = msub.add_parser("remover")
    m.add_argument("nome")
    m.set_defaults(func=cmd_maquina_remover)

    s = sub.add_parser("agente", help="quem pode mandar comandos pra esta máquina (caminho manual)")
    asub = s.add_subparsers(dest="sub", required=True)
    m = asub.add_parser("autorizar")
    m.add_argument("nome")
    m.add_argument(
        "--segredo", nargs="?", const="-", help="usa um segredo já existente, lido do stdin (padrão: gera um novo)"
    )
    m.add_argument("--comandos", help='lista separada por vírgula, ou "*" pra todos (padrão)')
    m.add_argument("--no-tailscale", help="nome do nó no tailscale, se for diferente")
    m.set_defaults(func=cmd_agente_autorizar)
    m = asub.add_parser("revogar")
    m.add_argument("nome")
    m.set_defaults(func=cmd_agente_revogar)
    m = asub.add_parser("listar")
    m.set_defaults(func=cmd_maquinas)
    m = asub.add_parser("permissoes", help="mostra ou muda quais comandos uma máquina pareada pode rodar aqui")
    m.add_argument("nome")
    m.add_argument("comandos", nargs="*", help='nomes dos comandos, ou "*" pra todos (sem nada: só mostra)')
    m.set_defaults(func=cmd_agente_permissoes)

    s = sub.add_parser("config", help="mostra o config sem os segredos")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("parear", help="pareia esta máquina com outra (as duas passam a mandar uma na outra)")
    s.add_argument(
        "maquina", nargs="?", help="a outra máquina, ou só um pedaço do nome (g15, hp). Sem isso, mostra o código"
    )
    s.add_argument("--com", help=argparse.SUPPRESS)  # jeito antigo de passar a máquina
    s.add_argument("--porta", type=int, help="porta de pareamento (padrão 47801)")
    s.add_argument("--tempo", type=float, default=120, help="segundos que o código vale (padrão 120)")
    s.add_argument("--escutar", help="IP pra escutar o pareamento, se não for o do Tailscale")
    s.set_defaults(func=cmd_parear)

    s = sub.add_parser("exemplos", help="cria (ou atualiza) os comandos de exemplo nesta máquina")
    s.add_argument("--atualizar", action="store_true", help="troca os existentes (os atuais viram .bak)")
    s.add_argument("--sim", action="store_true")
    s.set_defaults(func=cmd_exemplos)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        a.func(a)
    except Exit as e:
        if str(e):
            warn(str(e))
        sys.exit(e.code)
    except KeyboardInterrupt:
        sys.exit(130)
    except (ConfigError, ParamError) as e:
        warn(str(e))
        sys.exit(2)
    except ConnectionFailed as e:
        warn(str(e))
        sys.exit(3)
    except (AuthError, ProtocolError) as e:
        warn(str(e))
        sys.exit(4)
    except (RemoteError, CommandError) as e:
        warn(f"recusado: {e}")
        sys.exit(1)
    except RelayError as e:
        warn(str(e))
        sys.exit(1)
    finally:
        with contextlib.suppress(Exception):
            sys.stdout.flush()
