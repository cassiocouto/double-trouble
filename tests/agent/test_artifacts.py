"""Canal de volta pra arquivos binários: o comando escreve, o controlador recebe."""

from __future__ import annotations

import os

import pytest

from relay.core.media import MAX_ARTIFACT_BYTES

from ..conftest import wait_until

PNG = b"\x89PNG\r\n\x1a\n" + b"conteudo de imagem de teste" * 4

WRITE_PNG = f"""
import base64, os
RELAY_PNG = {PNG!r}
with open(os.environ["RELAY_OUTPUT_FILE"], "wb") as f:
    f.write(RELAY_PNG)
print("escrevi a imagem")
"""


def test_command_returns_an_image(agent):
    h = agent()
    h.make_cmd("tela", WRITE_PNG)
    out = h.client().run("tela")
    assert out.text_stdout == "escrevi a imagem\n"
    assert out.artifact == PNG
    assert out.artifact_type == "image/png"
    assert out.artifact_is_image is True
    assert out.result["artefato"] == {"tipo": "image/png", "imagem": True, "bytes": len(PNG)}
    assert out.result["estado"] == "ok"


def test_no_output_file_means_no_artifact(agent):
    h = agent()
    h.make_cmd("oi", "print('oi')\n")
    out = h.client().run("oi")
    assert out.artifact is None
    assert "artefato" not in out.result


def test_artifact_survives_status_after_the_fact(agent):
    h = agent()
    h.make_cmd("tela", WRITE_PNG)
    job = h.client().run("tela", follow=False).job
    assert wait_until(lambda: h.client().status(job).result.get("estado") == "ok")
    again = h.client().status(job)
    assert again.artifact == PNG


def test_type_is_sniffed_not_trusted(agent):
    """O comando escreve bytes que não são imagem: o agente não marca como imagem."""
    h = agent()
    h.make_cmd("texto", 'import os\nopen(os.environ["RELAY_OUTPUT_FILE"], "w").write("so texto\\n")\n')
    out = h.client().run("texto")
    assert out.artifact == b"so texto\n"
    assert out.artifact_type == "text/plain" and out.artifact_is_image is False


def test_big_artifact_is_refused_with_a_reason(agent):
    h = agent()
    h.make_cmd(
        "enorme",
        f'import os\nopen(os.environ["RELAY_OUTPUT_FILE"], "wb").write(b"x" * ({MAX_ARTIFACT_BYTES} + 10))\n',
    )
    out = h.client().run("enorme")
    assert out.artifact is None
    assert "máximo" in out.result["artefato_erro"]
    assert out.result["estado"] == "ok"  # o comando em si deu certo


def test_symlink_output_is_refused(agent, tmp_path):
    secret = tmp_path / "segredo"
    secret.write_text("não era pra vazar")
    h = agent()
    h.make_cmd(
        "link",
        f'import os\np = os.environ["RELAY_OUTPUT_FILE"]\nos.symlink({str(secret)!r}, p)\n',
    )
    out = h.client().run("link")
    assert out.artifact is None
    assert "link simbólico" in out.result["artefato_erro"]


def test_multichunk_image(agent):
    # Mais de um pedaço (ARTIFACT_CHUNK = 512 KiB). Os bytes são gerados pelo
    # comando, não embutidos no arquivo dele (que tem limite de 1 MiB).
    h = agent()
    h.make_cmd(
        "grande",
        "import os\n"
        'data = b"\\x89PNG\\r\\n\\x1a\\n" + bytes(range(256)) * 6000\n'
        'open(os.environ["RELAY_OUTPUT_FILE"], "wb").write(data)\n'
        "print(len(data))\n",
    )
    out = h.client().run("grande")
    assert out.artifact is not None
    assert len(out.artifact) == int(out.text_stdout) > 1024 * 1024
    assert out.artifact.startswith(b"\x89PNG")


def test_nonzero_exit_still_returns_artifact(agent):
    """Código != 0 ainda devolve o arquivo (o comando rodou até o fim)."""
    h = agent()
    h.make_cmd(
        "aviso",
        'import os, sys\nopen(os.environ["RELAY_OUTPUT_FILE"], "wb").write(b"%PDF-1.4 resto")\nsys.exit(2)\n',
    )
    out = h.client().run("aviso")
    assert out.result["codigo"] == 2
    assert out.artifact == b"%PDF-1.4 resto" and out.artifact_type == "application/pdf"


def test_timeout_discards_artifact(agent):
    """Em timeout o arquivo pode estar pela metade: nem lê."""
    h = agent()
    h.make_cmd(
        "demora",
        "import os, time\nTIMEOUT = 1\n"
        'open(os.environ["RELAY_OUTPUT_FILE"], "wb").write(b"\\x89PNG pela metade")\n'
        "time.sleep(30)\n",
    )
    out = h.client().run("demora")
    assert out.result["estado"] == "tempo_esgotado"
    assert out.artifact is None


def test_temp_dir_is_cleaned_up(agent):
    h = agent()
    h.make_cmd("tela", WRITE_PNG)
    before = set(os.listdir("/tmp"))
    h.client().run("tela")
    leftover = [d for d in set(os.listdir("/tmp")) - before if d.startswith("relay-saida-")]
    assert leftover == []


@pytest.mark.parametrize(
    "magic, tipo, imagem",
    [
        (b"\xff\xd8\xff\xe0", "image/jpeg", True),
        (b"GIF89a", "image/gif", True),
        (b"%PDF-1.7", "application/pdf", False),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp", True),
    ],
)
def test_sniff_various_types(agent, magic, tipo, imagem):
    h = agent()
    h.make_cmd(
        "arq",
        f'import os\nopen(os.environ["RELAY_OUTPUT_FILE"], "wb").write({magic!r} + b"resto")\n',
    )
    out = h.client().run("arq")
    assert out.artifact_type == tipo and out.artifact_is_image is imagem
