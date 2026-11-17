"""O nome antigo do projeto não pode voltar pra nenhum arquivo do repositório."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OLD_NAME = "".join(["p", "o", "n", "t", "e"])  # montado em pedaços pra este arquivo não conter a palavra


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            check=True,
        ).stdout
        names = [n for n in out.decode().split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = [str(p.relative_to(REPO)) for p in REPO.rglob("*") if ".git" not in p.parts and ".venv" not in p.parts]
    return [REPO / n for n in names if (REPO / n).is_file()]


def test_old_name_is_gone():
    found = []
    for path in tracked_files():
        if OLD_NAME in str(path.relative_to(REPO)).lower():
            found.append(str(path.relative_to(REPO)))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if OLD_NAME in line.lower():
                found.append(f"{path.relative_to(REPO)}:{number}")
    assert not found, "o nome antigo voltou em: " + ", ".join(found[:20])
