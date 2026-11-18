"""O recorte nunca pode passar da borda da imagem.

Se passar, o GdkPixbuf.new_subpixbuf lê fora da memória e o programa cai com
segfault. Aqui a gente tenta estourar as bordas de todo jeito e confere que o
retângulo devolvido sempre cabe dentro de [0, iw) x [0, ih).
"""

from __future__ import annotations

import random

from relay.gui.geometry import crop_rect

IW, IH = 1920, 1080


def dentro(rect, iw=IW, ih=IH) -> bool:
    px, py, pw, ph = rect
    return px >= 0 and py >= 0 and pw >= 1 and ph >= 1 and px + pw <= iw and py + ph <= ih


def test_selecao_normal_no_meio():
    # escala 1, sem bordas: a seleção vira os mesmos pixels
    rect = crop_rect((100, 200), (500, 350), 1.0, 0.0, 0.0, IW, IH)
    assert rect == (100, 200, 400, 150)
    assert dentro(rect)


def test_arrastar_passando_do_canto_inferior_direito():
    # o mouse foi muito além da imagem: tem que grampear na borda, não estourar
    rect = crop_rect((1900.6, 1070.4), (3000.0, 2000.0), 1.0, 0.0, 0.0, IW, IH)
    assert rect is not None
    assert dentro(rect)
    assert rect[0] + rect[2] == IW or rect[0] + rect[2] < IW


def test_borda_de_baixo_com_meio_pixel():
    # o caso do título lá embaixo: seleção encostando na borda inferior
    for y0 in (1000.0, 1000.4, 1000.5, 1000.6, 1079.4, 1079.5):
        rect = crop_rect((200.0, y0), (900.0, 5000.0), 1.0, 0.0, 0.0, IW, IH)
        if rect is not None:
            assert dentro(rect), f"estourou com y0={y0}: {rect}"


def test_imagem_inteira():
    # imagem desenhada com escala 0.5 e bordas (ox, oy); selecionar tudo
    rect = crop_rect((10.0, 20.0), (10 + IW * 0.5, 20 + IH * 0.5), 0.5, 10.0, 20.0, IW, IH)
    assert rect is not None
    assert dentro(rect)
    assert rect == (0, 0, IW, IH)


def test_selecao_vazia_vira_none():
    assert crop_rect((100, 100), (100, 100), 1.0, 0.0, 0.0, IW, IH) is None
    assert crop_rect((100, 100), (101, 101), 1.0, 0.0, 0.0, IW, IH) == (100, 100, 1, 1)


def test_imagem_sem_tamanho_ou_escala_zero():
    assert crop_rect((0, 0), (10, 10), 1.0, 0.0, 0.0, 0, 0) is None
    assert crop_rect((0, 0), (10, 10), 0.0, 0.0, 0.0, IW, IH) is None


def test_fuzz_nunca_estoura():
    rnd = random.Random(20260101)
    for _ in range(5000):
        scale = rnd.choice([0.25, 0.4, 0.5, 0.75, 1.0, 1.0, 2.0])
        ox = rnd.uniform(-50, 400)
        oy = rnd.uniform(-50, 400)
        a = (rnd.uniform(-200, 2600), rnd.uniform(-200, 1600))
        b = (rnd.uniform(-200, 2600), rnd.uniform(-200, 1600))
        rect = crop_rect(a, b, scale, ox, oy, IW, IH)
        if rect is not None:
            assert dentro(rect), f"estourou: start={a} cur={b} scale={scale} ox={ox} oy={oy} -> {rect}"
