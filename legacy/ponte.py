#!/usr/bin/env python3
"""ponte.py - uma maquina chama programas pre-aprovados na outra.

Feito pra cass-dell-brasil e cass-dell-g15-5511. O mesmo arquivo roda nas
duas. Cada maquina e ao mesmo tempo servidor (escuta pedidos) e cliente
(manda pedidos pra outra).

Regras de seguranca
  * So roda arquivos que estao em ~/.config/ponte/comandos/*.py
  * Nada passa por shell, o nome do comando e validado por regex
  * Pedidos e respostas sao assinados com HMAC-SHA256 (segredo de 32 bytes)
  * Cada pedido tem horario e nonce, entao nao da pra repetir um pedido velho
  * O servidor escuta so no IP do Tailscale e so aceita IPs do tailnet
  * Arquivos de comando com permissao de escrita pra outros sao recusados

Instalar (nas duas maquinas, ponha este arquivo em ~/bin/ponte.py por exemplo)
  1. Na primeira maquina      python3 ~/bin/ponte.py init
     Ela mostra o comando pra rodar na segunda, com o mesmo segredo.
  2. Na segunda maquina       python3 ~/bin/ponte.py init --segredo <hex>
  3. Nas duas                 python3 ~/bin/ponte.py servico

Usar
  ponte.py ping g15
  ponte.py listar g15
  ponte.py rodar g15 status
  ponte.py rodar g15 notificar "oi do outro lado"
  ponte.py rodar brasil abrir_url https://example.com

O nome da maquina pode ser so um pedaco (g15, brasil). "local" chama a propria.

Criar um comando novo
  Crie ~/.config/ponte/comandos/meu_comando.py (letras minusculas, numeros,
  _ e -). Ele e lido sem ser executado pra pegar estes metadados opcionais
    docstring do modulo   descricao mostrada no listar
    ACEITA_ARGS = True    permite argumentos (padrao False, recusa qualquer um)
    TIMEOUT = 30          limite em segundos (padrao 60, maximo 3600)
  O que ele imprimir volta pra quem chamou. O codigo de saida tambem.
  A variavel de ambiente PONTE_DE diz qual maquina chamou.

Logs do servico
  journalctl --user -u ponte -f
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROTOCOL_VERSION = 1
BASE_DIR = Path(os.environ.get("PONTE_DIR") or Path.home() / ".config" / "ponte")
CONFIG_FILE = BASE_DIR / "config.json"
COMMANDS_DIR = BASE_DIR / "comandos"

DEFAULT_PORT = 47800
KNOWN_MACHINES = ("cass-dell-brasil", "cass-dell-g15-5511")
# Faixas do Tailscale (IPv4 e IPv6) e loopback pra testar com "local".
DEFAULT_ALLOWED = ["100.64.0.0/10", "fd7a:115c:a1e0::/48", "127.0.0.0/8", "::1/128"]

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_ARGS = 32
MAX_ARG_CHARS = 4096
MAX_CLOCK_SKEW = 60
NONCE_TTL = 3 * MAX_CLOCK_SKEW
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 3600
MAX_PARALLEL = 4
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

# Variaveis da sessao grafica que os comandos precisam pra mexer na tela.
SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
)


class PonteError(Exception):
    pass


# --------------------------------------------------------------------------
# utilidades


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 2) -> None:
    print(f"ponte: {msg}", file=sys.stderr)
    sys.exit(code)


def unsafe_reason(path: Path, private: bool, follow: bool = True) -> str | None:
    """Diz por que um caminho nao e confiavel, ou None se estiver ok."""
    try:
        st = path.stat() if follow else path.lstat()
    except FileNotFoundError:
        return "nao existe"
    if stat.S_ISLNK(st.st_mode):
        return "e um link simbolico"
    if st.st_uid != os.getuid():
        return "pertence a outro usuario"
    bad_bits = 0o077 if private else 0o022
    if st.st_mode & bad_bits:
        return f"permissoes abertas demais ({oct(st.st_mode & 0o777)})"
    return None


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        die(f"nao achei {CONFIG_FILE}, rode 'ponte.py init' primeiro")
    reason = unsafe_reason(CONFIG_FILE, private=True)
    if reason:
        die(f"{CONFIG_FILE} {reason}, rode 'chmod 600 {CONFIG_FILE}'")
    with CONFIG_FILE.open(encoding="utf-8") as f:
        cfg = json.load(f)
    try:
        key = bytes.fromhex(cfg["segredo"])
    except (KeyError, ValueError, TypeError):
        die("o segredo no config nao e hexadecimal valido")
    if len(key) < 32:
        die("o segredo precisa ter pelo menos 32 bytes (64 caracteres hex)")
    cfg["_key"] = key
    cfg.setdefault("nome", socket.gethostname())
    cfg.setdefault("escutar", "127.0.0.1")
    cfg.setdefault("porta", DEFAULT_PORT)
    cfg.setdefault("permitidos", DEFAULT_ALLOWED)
    cfg.setdefault("maquinas", {})
    return cfg


# --------------------------------------------------------------------------
# protocolo: uma linha JSON pra la, uma linha JSON pra ca


def canonical(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def compute_mac(key: bytes, kind: str, obj: dict) -> str:
    return hmac.new(key, kind.encode() + b"\n" + canonical(obj), hashlib.sha256).hexdigest()


def sign(key: bytes, kind: str, obj: dict) -> dict:
    obj["mac"] = compute_mac(key, kind, obj)
    return obj


def verify(key: bytes, kind: str, obj: dict) -> bool:
    mac = obj.get("mac")
    if not isinstance(mac, str):
        return False
    return hmac.compare_digest(mac, compute_mac(key, kind, obj))


def send_msg(sock: socket.socket, obj: dict) -> None:
    sock.sendall(json.dumps(obj, ensure_ascii=True).encode() + b"\n")


def recv_msg(sock: socket.socket, limit: int) -> dict:
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise PonteError("mensagem grande demais")
    line = bytes(buf).split(b"\n", 1)[0]
    if not line:
        raise PonteError("conexao fechada sem mensagem")
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise PonteError("mensagem nao e um objeto JSON")
    return obj


# --------------------------------------------------------------------------
# comandos


def read_metadata(path: Path) -> dict:
    meta = {"descricao": "", "aceita_args": False, "timeout": DEFAULT_TIMEOUT}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError) as e:
        raise PonteError(f"'{path.stem}' tem erro de sintaxe ({e})")
    doc = (ast.get_docstring(tree) or "").strip()
    if doc:
        meta["descricao"] = doc.splitlines()[0]
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value_node = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value_node = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in ("ACEITA_ARGS", "TIMEOUT"):
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, SyntaxError):
            continue
        if target.id == "ACEITA_ARGS" and isinstance(value, bool):
            meta["aceita_args"] = value
        elif (
            target.id == "TIMEOUT"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 < value <= MAX_TIMEOUT
        ):
            meta["timeout"] = value
    return meta


def resolve_command(name: str) -> tuple[Path, dict]:
    if not NAME_RE.fullmatch(name):
        raise PonteError("nome de comando invalido")
    for d in (BASE_DIR, COMMANDS_DIR):
        reason = unsafe_reason(d, private=False)
        if reason:
            raise PonteError(f"a pasta {d} {reason}")
    path = COMMANDS_DIR / f"{name}.py"
    if not path.exists() and not path.is_symlink():
        raise PonteError(f"o comando '{name}' nao existe")
    reason = unsafe_reason(path, private=False, follow=False)
    if reason:
        raise PonteError(f"o arquivo de '{name}' {reason}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise PonteError(f"'{name}' nao e um arquivo comum")
    if path.resolve().parent != COMMANDS_DIR.resolve():
        raise PonteError(f"'{name}' aponta pra fora da pasta de comandos")
    return path, read_metadata(path)


def list_commands() -> list[dict]:
    out = []
    if not COMMANDS_DIR.is_dir():
        return out
    for p in sorted(COMMANDS_DIR.glob("*.py")):
        if not NAME_RE.fullmatch(p.stem):
            continue
        try:
            _, meta = resolve_command(p.stem)
            out.append({"nome": p.stem, **meta})
        except PonteError as e:
            out.append({"nome": p.stem, "bloqueado": str(e)})
    return out


def session_env() -> dict:
    """Pega DISPLAY, DBUS etc. do systemd do usuario no momento do pedido.

    O servico pode ter subido antes da sessao grafica, entao o ambiente dele
    pode estar velho. Perguntar ao systemd na hora resolve isso.
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    env = {}
    for line in r.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SESSION_ENV_KEYS and not value.startswith("$'"):
            env[key] = value
    return env


def read_head(f) -> tuple[str, bool]:
    f.seek(0)
    data = f.read(MAX_OUTPUT_BYTES + 1)
    truncated = len(data) > MAX_OUTPUT_BYTES
    return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), truncated


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(path: Path, args: list[str], timeout: float, caller: str) -> dict:
    env = os.environ.copy()
    env.update(session_env())
    env["PONTE_DE"] = caller
    # Saida vai pra arquivo temporario, nao pra pipe. Assim um programa que o
    # comando deixou rodando (um navegador, por exemplo) nao segura a resposta.
    with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        start = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, str(path), *args],
            cwd=str(COMMANDS_DIR),
            stdin=subprocess.DEVNULL,
            stdout=fout,
            stderr=ferr,
            env=env,
            start_new_session=True,
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_group(proc)
            proc.wait()
        duration = time.monotonic() - start
        stdout, t1 = read_head(fout)
        stderr, t2 = read_head(ferr)
    if timed_out:
        stderr += f"\n[ponte] passou de {timeout}s e foi encerrado\n"
    code = proc.returncode
    return {
        "ok": code == 0 and not timed_out,
        "codigo": code,
        "stdout": stdout,
        "stderr": stderr,
        "truncado": t1 or t2,
        "estourou_tempo": timed_out,
        "duracao": round(duration, 3),
    }


# --------------------------------------------------------------------------
# servidor


class NonceCache:
    def __init__(self, ttl: float, max_size: int = 100_000):
        self.ttl = ttl
        self.max_size = max_size
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()

    def add(self, nonce: str) -> bool:
        now = time.time()
        with self.lock:
            for n, t in list(self.seen.items()):
                if now - t > self.ttl:
                    del self.seen[n]
            if nonce in self.seen or len(self.seen) >= self.max_size:
                return False
            self.seen[nonce] = now
            return True


class Handler(socketserver.BaseRequestHandler):
    server: "PonteServer"

    def handle(self) -> None:
        peer_ip = self.client_address[0]
        if not self.server.ip_allowed(peer_ip):
            log(f"recusado {peer_ip}, fora da lista de permitidos")
            return
        self.request.settimeout(15)
        try:
            req = recv_msg(self.request, MAX_REQUEST_BYTES)
        except (PonteError, ValueError, OSError) as e:
            log(f"pedido ilegivel de {peer_ip} ({e})")
            return
        try:
            resp = self.server.process(req, peer_ip)
        except Exception as e:  # nunca deixar o servidor cair por um pedido
            log(f"erro interno tratando pedido de {peer_ip} ({e!r})")
            resp = sign(self.server.key, "resp", {
                "v": PROTOCOL_VERSION, "nonce": str(req.get("nonce", ""))[:32],
                "de": self.server.cfg["nome"], "ok": False, "erro": "erro interno no servidor",
            })
        try:
            send_msg(self.request, resp)
        except OSError as e:
            log(f"nao consegui responder {peer_ip} ({e})")


class PonteServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key: bytes = cfg["_key"]
        self.allowed = [ipaddress.ip_network(n, strict=False) for n in cfg["permitidos"]]
        self.nonces = NonceCache(NONCE_TTL)
        self.slots = threading.BoundedSemaphore(MAX_PARALLEL)
        host = cfg["escutar"]
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, int(cfg["porta"])), Handler)

    def ip_allowed(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.allowed)

    def process(self, req: dict, peer_ip: str) -> dict:
        if not verify(self.key, "req", req):
            log(f"assinatura invalida vinda de {peer_ip}")
            # Sem assinatura na resposta, quem nao tem o segredo nao ganha nada.
            return {"v": PROTOCOL_VERSION, "ok": False, "erro": "assinatura invalida"}

        nonce = req.get("nonce")
        nonce = nonce if isinstance(nonce, str) else ""

        def reply(ok: bool, **fields) -> dict:
            body = {"v": PROTOCOL_VERSION, "nonce": nonce, "de": self.cfg["nome"], "ok": ok, **fields}
            return sign(self.key, "resp", body)

        if req.get("v") != PROTOCOL_VERSION:
            return reply(False, erro="versao de protocolo diferente, atualize o ponte.py nas duas maquinas")
        ts = req.get("ts")
        if not isinstance(ts, (int, float)) or abs(time.time() - ts) > MAX_CLOCK_SKEW:
            return reply(False, erro="pedido velho ou relogios fora de sincronia")
        if not NONCE_RE.fullmatch(nonce):
            return reply(False, erro="nonce invalido")
        if not self.nonces.add(nonce):
            log(f"pedido repetido vindo de {peer_ip}, ignorado")
            return reply(False, erro="pedido repetido")

        caller = str(req.get("de", "?"))[:64]
        action = req.get("acao")
        if action == "ping":
            return reply(True, hora=time.time(), versao=PROTOCOL_VERSION)
        if action == "listar":
            return reply(True, comandos=list_commands())
        if action == "rodar":
            return reply(**self.run(req, caller, peer_ip))
        return reply(False, erro="acao desconhecida")

    def run(self, req: dict, caller: str, peer_ip: str) -> dict:
        name = req.get("comando")
        args = req.get("args", [])
        if not isinstance(name, str):
            return {"ok": False, "erro": "nome de comando invalido"}
        if (
            not isinstance(args, list)
            or len(args) > MAX_ARGS
            or not all(isinstance(a, str) and len(a) <= MAX_ARG_CHARS and "\x00" not in a for a in args)
        ):
            return {"ok": False, "erro": f"argumentos invalidos (maximo {MAX_ARGS}, cada um ate {MAX_ARG_CHARS} caracteres)"}
        try:
            path, meta = resolve_command(name)
        except PonteError as e:
            log(f"{caller}@{peer_ip} pediu '{name}' e foi recusado ({e})")
            return {"ok": False, "erro": str(e)}
        if args and not meta["aceita_args"]:
            return {"ok": False, "erro": f"'{name}' nao aceita argumentos (ponha ACEITA_ARGS = True no arquivo)"}
        if not self.slots.acquire(blocking=False):
            return {"ok": False, "erro": f"ja tem {MAX_PARALLEL} comandos rodando, tente de novo"}
        try:
            log(f"{caller}@{peer_ip} rodando {name} {args!r}")
            result = execute(path, args, meta["timeout"], caller)
        finally:
            self.slots.release()
        log(f"{name} terminou com codigo {result['codigo']} em {result['duracao']:.2f}s")
        return result


# --------------------------------------------------------------------------
# cliente


def find_machine(cfg: dict, query: str) -> tuple[str, str, int]:
    if query in ("local", cfg["nome"]):
        return cfg["nome"], cfg["escutar"], int(cfg["porta"])
    machines = cfg["maquinas"]
    if query in machines:
        name = query
    else:
        hits = [n for n in machines if query in n]
        if not hits:
            die(f"nao conheco a maquina '{query}'. Conheco {', '.join(machines) or 'nenhuma'}")
        if len(hits) > 1:
            die(f"'{query}' bate com mais de uma maquina ({', '.join(hits)})")
        name = hits[0]
    m = machines[name]
    return name, m.get("endereco", name), int(m.get("porta", DEFAULT_PORT))


def call(cfg: dict, query: str, body: dict, wait: float) -> tuple[str, dict]:
    name, host, port = find_machine(cfg, query)
    req = {
        "v": PROTOCOL_VERSION,
        "de": cfg["nome"],
        "ts": int(time.time()),
        "nonce": secrets.token_hex(16),
        **body,
    }
    sign(cfg["_key"], "req", req)
    try:
        with socket.create_connection((host, port), timeout=10) as s:
            s.settimeout(wait)
            send_msg(s, req)
            resp = recv_msg(s, MAX_RESPONSE_BYTES)
    except socket.timeout:
        die(f"{name} nao respondeu a tempo", 3)
    except OSError as e:
        die(f"nao consegui falar com {name} em {host}:{port} ({e})", 3)
    except (PonteError, ValueError) as e:
        die(f"resposta estranha de {name} ({e})", 3)
    if not verify(cfg["_key"], "resp", resp) or resp.get("nonce") != req["nonce"]:
        if resp.get("ok") is False and isinstance(resp.get("erro"), str):
            die(f"{name} recusou ({resp['erro']}). Confira se o segredo e o mesmo nas duas maquinas", 4)
        die(f"a resposta de {name} nao tem assinatura valida, ignorei", 4)
    return name, resp


def cmd_ping(a) -> None:
    cfg = load_config()
    t0 = time.monotonic()
    name, resp = call(cfg, a.maquina, {"acao": "ping"}, 15)
    ms = (time.monotonic() - t0) * 1000
    if not resp.get("ok"):
        die(f"{name} respondeu com erro ({resp.get('erro')})", 1)
    skew = abs(time.time() - float(resp.get("hora", time.time())))
    print(f"{name} respondeu em {ms:.0f} ms (relogios a {skew:.1f} s de diferenca)")


def cmd_listar(a) -> None:
    cfg = load_config()
    name, resp = call(cfg, a.maquina, {"acao": "listar"}, 15)
    if not resp.get("ok"):
        die(f"{name} respondeu com erro ({resp.get('erro')})", 1)
    cmds = resp.get("comandos", [])
    if not cmds:
        print(f"{name} nao tem nenhum comando ainda")
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
        if c.get("timeout") != DEFAULT_TIMEOUT:
            extras.append(f"limite {c.get('timeout')}s")
        tail = f"  [{', '.join(extras)}]" if extras else ""
        print(f"  {c['nome']:<{width}}  {c.get('descricao') or '(sem descricao)'}{tail}")


def cmd_rodar(a) -> None:
    cfg = load_config()
    body = {"acao": "rodar", "comando": a.comando, "args": a.args}
    name, resp = call(cfg, a.maquina, body, a.espera)
    if "codigo" not in resp:
        die(f"{name} recusou ({resp.get('erro', 'sem detalhe')})", 1)
    sys.stdout.write(resp.get("stdout", ""))
    sys.stdout.flush()
    sys.stderr.write(resp.get("stderr", ""))
    if resp.get("truncado"):
        print(f"\nponte: a saida foi cortada em {MAX_OUTPUT_BYTES // 1024} KiB", file=sys.stderr)
    code = resp.get("codigo")
    if not isinstance(code, int):
        sys.exit(1)
    sys.exit(128 - code if code < 0 else min(code, 255))


# --------------------------------------------------------------------------
# init e servico


def tailscale_ip() -> str | None:
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = r.stdout.split()
    if r.returncode != 0 or not lines:
        return None
    try:
        return str(ipaddress.ip_address(lines[0]))
    except ValueError:
        return None


def parse_peer(spec: str) -> tuple[str, dict]:
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        die(f"--par precisa ser NOME=ENDERECO ou NOME=ENDERECO:PORTA, veio '{spec}'")
    host, port = rest, DEFAULT_PORT
    if rest.count(":") == 1:
        host, port_s = rest.split(":")
        port = int(port_s)
    return name, {"endereco": host, "porta": port}


def cmd_init(a) -> None:
    if CONFIG_FILE.exists() and not a.forcar:
        die(f"{CONFIG_FILE} ja existe, use --forcar pra sobrescrever")
    if a.segredo == "-":
        a.segredo = sys.stdin.readline().strip()
    secret = a.segredo or secrets.token_hex(32)
    try:
        if len(bytes.fromhex(secret)) < 32:
            raise ValueError
    except ValueError:
        die("--segredo precisa ser hexadecimal com pelo menos 64 caracteres")

    name = a.nome or socket.gethostname()
    listen = a.escutar or tailscale_ip()
    if not listen:
        listen = "127.0.0.1"
        print("aviso: nao achei o IP do Tailscale, vou escutar so em 127.0.0.1. "
              "Rode de novo com --escutar <ip do tailscale> --forcar", file=sys.stderr)

    machines = dict(parse_peer(p) for p in (a.par or []))
    if not machines:
        for other in KNOWN_MACHINES:
            if other != name:
                machines[other] = {"endereco": other, "porta": a.porta}

    BASE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BASE_DIR, 0o700)
    COMMANDS_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(COMMANDS_DIR, 0o700)

    cfg = {
        "nome": name,
        "escutar": listen,
        "porta": a.porta,
        "segredo": secret,
        "permitidos": DEFAULT_ALLOWED,
        "maquinas": machines,
    }
    write_private(CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")

    created = []
    for cmd_name, code in EXAMPLE_COMMANDS.items():
        p = COMMANDS_DIR / f"{cmd_name}.py"
        if not p.exists():
            write_private(p, code.lstrip("\n"))
            created.append(cmd_name)

    print(f"pronto, config em {CONFIG_FILE}")
    print(f"  esta maquina   {name}, escutando em {listen}:{a.porta}")
    print(f"  conhece        {', '.join(machines) or 'nenhuma'}")
    if created:
        print(f"  exemplos       {', '.join(created)} em {COMMANDS_DIR}")
    if not a.segredo:
        script = Path(__file__).resolve()
        print("\nNa outra maquina rode (mesmo segredo)\n")
        print(f"  python3 {script} init --segredo {secret}\n")
        print("Depois, nas duas,  python3 ponte.py servico")


UNIT_TEMPLATE = """\
[Unit]
Description=Ponte entre os desktops da Cass

[Service]
ExecStart={exec_start}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
{extra_env}
[Install]
WantedBy=default.target
"""


def systemd_quote(s: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./=:+-]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cmd_servico(a) -> None:
    load_config()  # falha cedo se o config nao existe ou esta inseguro
    script = Path(__file__).resolve()
    exec_start = " ".join(systemd_quote(x) for x in (sys.executable, str(script), "servir"))
    extra_env = ""
    if os.environ.get("PONTE_DIR"):
        extra_env = f"Environment=PONTE_DIR={systemd_quote(str(BASE_DIR))}\n"
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "ponte.service"
    unit.write_text(UNIT_TEMPLATE.format(exec_start=exec_start, extra_env=extra_env), encoding="utf-8")
    print(f"escrevi {unit}")
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", "ponte.service"],
                ["systemctl", "--user", "restart", "ponte.service"]):
        r = subprocess.run(cmd)
        if r.returncode != 0:
            die(f"'{' '.join(cmd)}' falhou, veja a mensagem acima", 1)
    print("servico ligado. Pra ver os logs,  journalctl --user -u ponte -f")


def cmd_servir(a) -> None:
    cfg = load_config()
    COMMANDS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for d in (BASE_DIR, COMMANDS_DIR):
        reason = unsafe_reason(d, private=False)
        if reason:
            log(f"aviso, {d} {reason}. Nenhum comando vai rodar ate isso ser corrigido")
    try:
        srv = PonteServer(cfg)
    except OSError as e:
        die(f"nao consegui escutar em {cfg['escutar']}:{cfg['porta']} ({e})", 1)
    log(f"ponte {cfg['nome']} escutando em {cfg['escutar']}:{cfg['porta']}, comandos em {COMMANDS_DIR}")
    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("desligando")
    finally:
        srv.server_close()


# --------------------------------------------------------------------------
# comandos de exemplo criados pelo init


EXAMPLE_COMMANDS = {
    "status": r'''
"""Mostra nome, tempo ligada, carga, memoria e disco desta maquina."""
import os
import shutil
import socket


def uptime():
    with open("/proc/uptime") as f:
        secs = int(float(f.read().split()[0]))
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h {rest // 60}min"


def memory():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])
    total = info["MemTotal"] / 2**20
    free = info["MemAvailable"] / 2**20
    return f"{total - free:.1f} de {total:.1f} GiB em uso"


disk = shutil.disk_usage(os.path.expanduser("~"))
print(f"maquina  {socket.gethostname()}")
print(f"ligada   {uptime()}")
print(f"carga    {' '.join(f'{x:.2f}' for x in os.getloadavg())}")
print(f"memoria  {memory()}")
print(f"disco    {disk.used / 2**30:.0f} de {disk.total / 2**30:.0f} GiB em uso na home")
''',
    "notificar": r'''
"""Mostra uma notificacao na tela desta maquina."""
import os
import subprocess
import sys

ACEITA_ARGS = True
TIMEOUT = 15

text = " ".join(sys.argv[1:]) or "Oi!"
sender = os.environ.get("PONTE_DE", "a outra maquina")
subprocess.run(["notify-send", "--app-name=ponte", f"Mensagem de {sender}", text], check=True)
print("notificacao mostrada")
''',
    "travar_tela": r'''
"""Trava a tela desta maquina."""
import subprocess
import sys

TIMEOUT = 20

attempts = [
    ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
     "--object-path", "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.Lock"],
    ["loginctl", "lock-sessions"],
    ["xdg-screensaver", "lock"],
]
for cmd in attempts:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        continue
    if r.returncode == 0:
        print(f"tela travada (via {cmd[0]})")
        sys.exit(0)
print("nao consegui travar a tela", file=sys.stderr)
sys.exit(1)
''',
    "abrir_url": r'''
"""Abre um link http ou https no navegador desta maquina."""
import subprocess
import sys
from urllib.parse import urlparse

ACEITA_ARGS = True
TIMEOUT = 15

if len(sys.argv) != 2:
    print("uso  abrir_url <link>", file=sys.stderr)
    sys.exit(2)
url = sys.argv[1]
parts = urlparse(url)
if parts.scheme not in ("http", "https") or not parts.netloc:
    print("so aceito links http ou https", file=sys.stderr)
    sys.exit(2)
# Solta o navegador do comando, senao ele morre quando o comando acabar.
subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)
print(f"abrindo {url}")
''',
}


# --------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(prog="ponte", description="Uma maquina roda comandos pre-aprovados na outra.")
    sub = p.add_subparsers(dest="acao", required=True)

    s = sub.add_parser("init", help="cria a config e os comandos de exemplo")
    s.add_argument("--nome", help="nome desta maquina (padrao, o hostname)")
    s.add_argument("--segredo", help="segredo em hex vindo da outra maquina, ou - pra ler do stdin")
    s.add_argument("--escutar", help="IP pra escutar (padrao, o IP do Tailscale)")
    s.add_argument("--porta", type=int, default=DEFAULT_PORT)
    s.add_argument("--par", action="append", metavar="NOME=ENDERECO[:PORTA]",
                   help="outra maquina (padrao, a outra entre cass-dell-brasil e cass-dell-g15-5511)")
    s.add_argument("--forcar", action="store_true", help="sobrescreve uma config existente")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("servir", help="fica escutando pedidos (o servico chama isso)")
    s.set_defaults(func=cmd_servir)

    s = sub.add_parser("servico", help="instala e liga o servico systemd do usuario")
    s.set_defaults(func=cmd_servico)

    s = sub.add_parser("ping", help="ve se a outra maquina responde")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_ping)

    s = sub.add_parser("listar", help="mostra os comandos que a outra maquina aceita")
    s.add_argument("maquina")
    s.set_defaults(func=cmd_listar)

    s = sub.add_parser("rodar", help="roda um comando na outra maquina")
    s.add_argument("--espera", type=float, default=MAX_TIMEOUT + 30,
                   help="quanto tempo esperar a resposta, em segundos")
    s.add_argument("maquina")
    s.add_argument("comando")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_rodar)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
