"""Checagens de dono e permissão, e escrita de arquivos privados (invariante 7)."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import CommandError, ConfigError

# Arquivos de comando maiores que isso nem são lidos.
MAX_COMMAND_BYTES = 1024 * 1024


def describe_unsafe(st: os.stat_result, *, private: bool, uid: int | None = None) -> str | None:
    """Diz por que um arquivo ou pasta não é confiável, ou None se estiver ok.

    private=True exige que só o dono leia (modo 600/700). Senão, basta que
    grupo e outros não possam escrever.
    """
    if stat.S_ISLNK(st.st_mode):
        return "é um link simbólico"
    if st.st_uid != (os.getuid() if uid is None else uid):
        return "pertence a outro usuário"
    bad_bits = 0o077 if private else 0o022
    if st.st_mode & bad_bits:
        return f"tem permissões abertas demais ({oct(st.st_mode & 0o777)})"
    return None


def path_problem(path: Path, *, private: bool, follow: bool = True) -> str | None:
    try:
        st = os.stat(path) if follow else os.lstat(path)
    except FileNotFoundError:
        return "não existe"
    except OSError as e:
        return f"não pode ser lido ({e.strerror})"
    return describe_unsafe(st, private=private)


def write_private(path: Path, data: str | bytes, mode: int = 0o600) -> None:
    """Escreve o arquivo de forma atômica, já com o modo certo desde o início."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_private_text(path: Path, what: str) -> str:
    """Lê um arquivo que guarda segredo. Recusa se não for 600 e do usuário."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ConfigError(f"não achei {what} em {path}") from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ConfigError(f"{path} é um link simbólico, e {what} não pode ser link") from None
        raise ConfigError(f"não consegui ler {path} ({e.strerror})") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError(f"{path} não é um arquivo comum")
        reason = describe_unsafe(st, private=True)
        if reason:
            raise ConfigError(f"{path} {reason}. Rode: chmod 600 {path}")
        raw = f.read(MAX_COMMAND_BYTES + 1)
    if len(raw) > MAX_COMMAND_BYTES:
        raise ConfigError(f"{path} é grande demais")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError(f"{path} não é UTF-8 válido") from None


@dataclass
class OpenedCommand:
    """Arquivo de comando aberto e conferido. O fd é o que vai ser executado."""

    name: str
    path: Path
    fd: int
    source: bytes

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> OpenedCommand:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _open_dir(path: Path | str, dir_fd: int | None, label: Path) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        raise CommandError(f"a pasta {label} não existe") from None
    except NotADirectoryError:
        raise CommandError(f"{label} não é uma pasta") from None
    except OSError as e:
        raise CommandError(f"não consegui abrir a pasta {label} ({e.strerror})") from None
    reason = describe_unsafe(os.fstat(fd), private=False)
    if reason:
        os.close(fd)
        raise CommandError(f"a pasta {label} {reason}")
    return fd


def open_command_file(base: Path, name: str) -> OpenedCommand:
    """Abre <base>/comandos/<name>.py sem seguir link e confere tudo pelo fd.

    Quem chama já validou o nome. Conferir pelo fd (e depois executar esse
    mesmo fd) fecha a janela entre checar o arquivo e rodar o arquivo.
    """
    base_fd = _open_dir(base, None, base)
    try:
        cmd_fd = _open_dir("comandos", base_fd, base / "comandos")
    finally:
        os.close(base_fd)
    path = base / "comandos" / f"{name}.py"
    try:
        try:
            # O_NONBLOCK evita travar se alguém colocar um FIFO com esse nome.
            fd = os.open(
                f"{name}.py",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=cmd_fd,
            )
        except FileNotFoundError:
            raise CommandError(f"o comando '{name}' não existe") from None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise CommandError(f"o arquivo de '{name}' é um link simbólico") from None
            raise CommandError(f"não consegui abrir '{name}' ({e.strerror})") from None
    finally:
        os.close(cmd_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CommandError(f"'{name}' não é um arquivo comum")
        reason = describe_unsafe(st, private=False)
        if reason:
            raise CommandError(f"o arquivo de '{name}' {reason}")
        if st.st_size > MAX_COMMAND_BYTES:
            raise CommandError(f"o arquivo de '{name}' é grande demais")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_COMMAND_BYTES:
                raise CommandError(f"o arquivo de '{name}' é grande demais")
            chunks.append(chunk)
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return OpenedCommand(name=name, path=path, fd=fd, source=b"".join(chunks))
