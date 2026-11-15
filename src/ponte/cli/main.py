"""CLI da fase 1: mesmos comandos e mesmo protocolo do ponte.py v1."""

from __future__ import annotations

import argparse
import secrets
import socket
import sys
import time

from ..core import legacy, paths
from ..core.errors import AuthError, ConfigError, ConnectionFailed, PonteError, ProtocolError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_KIB = 256


def die(msg: str, code: int = 2):
    print(f"ponte: {msg}", file=sys.stderr)
    sys.exit(code)


def find_machine(cfg: dict, query: str) -> tuple[str, str, int]:
    if query in ("local", cfg["nome"]):
        return cfg["nome"], cfg["escutar"], int(cfg["porta"])
    machines = cfg["maquinas"]
    if query in machines:
        name = query
    else:
        hits = [n for n in machines if query in n]
        if not hits:
            raise ConfigError(f"não conheço a máquina '{query}'. Conheço {', '.join(machines) or 'nenhuma'}")
        if len(hits) > 1:
            raise ConfigError(f"'{query}' bate com mais de uma máquina ({', '.join(hits)})")
        name = hits[0]
    m = machines[name]
    return name, m.get("endereco", name), int(m.get("porta", 47800))


def call(cfg: dict, query: str, body: dict, wait: float) -> tuple[str, dict]:
    name, host, port = find_machine(cfg, query)
    req = {
        "v": legacy.V1_PROTOCOL,
        "de": cfg["nome"],
        "ts": int(time.time()),
        "nonce": secrets.token_hex(16),
        **body,
    }
    legacy.sign(cfg["_key"], "req", req)
    try:
        with socket.create_connection((host, port), timeout=10) as s:
            s.settimeout(wait)
            legacy.send_line(s, req)
            resp = legacy.recv_line(s, MAX_RESPONSE_BYTES)
    except TimeoutError:
        raise ConnectionFailed(f"{name} não respondeu a tempo") from None
    except OSError as e:
        raise ConnectionFailed(f"não consegui falar com {name} em {host}:{port} ({e})") from None
    if not legacy.verify(cfg["_key"], "resp", resp) or resp.get("nonce") != req["nonce"]:
        if resp.get("ok") is False and isinstance(resp.get("erro"), str):
            raise AuthError(
                f"{name} recusou ({resp['erro']}). Confira se o segredo é o mesmo nas duas máquinas"
            )
        raise AuthError(f"a resposta de {name} não tem assinatura válida, ignorei")
    return name, resp


def cmd_ping(a, cfg) -> None:
    t0 = time.monotonic()
    name, resp = call(cfg, a.maquina, {"acao": "ping"}, 15)
    ms = (time.monotonic() - t0) * 1000
    if not resp.get("ok"):
        die(f"{name} respondeu com erro ({resp.get('erro')})", 1)
    skew = abs(time.time() - float(resp.get("hora", time.time())))
    print(f"{name} respondeu em {ms:.0f} ms (relógios a {skew:.1f} s de diferença)")


def cmd_listar(a, cfg) -> None:
    name, resp = call(cfg, a.maquina, {"acao": "listar"}, 15)
    if not resp.get("ok"):
        die(f"{name} respondeu com erro ({resp.get('erro')})", 1)
    cmds = resp.get("comandos", [])
    if not cmds:
        print(f"{name} não tem nenhum comando ainda")
        return
    print(f"{name} tem {len(cmds)} comando(s)\n")
    width = max(len(c["nome"]) for c in cmds)
    for c in cmds:
        if "bloqueado" in c:
            print(f"  {c['nome']:<{width}}  BLOQUEADO, {c['bloqueado']}")
            continue
        extras = []
        if c.get("aceita_args"):
            extras.append("aceita argumentos")
        if c.get("timeout") != 60:
            extras.append(f"limite {c.get('timeout')}s")
        tail = f"  [{', '.join(extras)}]" if extras else ""
        print(f"  {c['nome']:<{width}}  {c.get('descricao') or '(sem descrição)'}{tail}")


def cmd_rodar(a, cfg) -> None:
    body = {"acao": "rodar", "comando": a.comando, "args": a.args}
    name, resp = call(cfg, a.maquina, body, a.espera)
    if "codigo" not in resp:
        die(f"{name} recusou ({resp.get('erro', 'sem detalhe')})", 1)
    sys.stdout.write(resp.get("stdout", ""))
    sys.stdout.flush()
    sys.stderr.write(resp.get("stderr", ""))
    if resp.get("truncado"):
        print(f"\nponte: a saída foi cortada em {MAX_OUTPUT_KIB} KiB", file=sys.stderr)
    code = resp.get("codigo")
    if not isinstance(code, int):
        sys.exit(1)
    sys.exit(128 - code if code < 0 else min(code, 255))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ponte", description="Uma máquina roda comandos pré-aprovados na outra.")
    sub = p.add_subparsers(dest="acao", required=True)
    s = sub.add_parser("ping", help="vê se a outra máquina responde")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_ping)
    s = sub.add_parser("listar", help="mostra os comandos que a outra máquina aceita")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_listar)
    s = sub.add_parser("rodar", help="roda um comando na outra máquina")
    s.add_argument("--espera", type=float, default=3600 + 30)
    s.add_argument("maquina")
    s.add_argument("comando")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_rodar)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    try:
        cfg = legacy.load_v1_config(paths.legacy_config_file())
        a.func(a, cfg)
    except ConfigError as e:
        die(str(e), 2)
    except ConnectionFailed as e:
        die(str(e), 3)
    except (AuthError, ProtocolError) as e:
        die(str(e), 4)
    except PonteError as e:
        die(str(e), 1)
