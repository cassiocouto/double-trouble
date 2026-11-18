"""Contas do recorte, sem GTK, pra dar pra testar de verdade.

A conversão da seleção (coordenadas da tela) pra pixels da imagem precisa
sempre caber dentro da imagem. Se passar da borda, o `GdkPixbuf.new_subpixbuf`
lê fora da memória e o programa cai com segfault. Por isso a conta mora aqui,
longe do GTK, e tem teste que tenta estourar as bordas.
"""

from __future__ import annotations


def crop_rect(
    start: tuple[float, float],
    cur: tuple[float, float],
    scale: float,
    ox: float,
    oy: float,
    iw: int,
    ih: int,
) -> tuple[int, int, int, int] | None:
    """Seleção (dois pontos na tela) → (x, y, w, h) em pixels da imagem.

    `scale`, `ox`, `oy` dizem como a imagem está desenhada (escala e bordas).
    O retângulo devolvido fica sempre dentro de [0, iw) x [0, ih). Devolve
    None se a seleção for vazia (largura ou altura <= 0).
    """
    if iw <= 0 or ih <= 0 or scale <= 0:
        return None
    xs = sorted((start[0], cur[0]))
    ys = sorted((start[1], cur[1]))
    x0 = max(0.0, (xs[0] - ox) / scale)
    y0 = max(0.0, (ys[0] - oy) / scale)
    x1 = min(float(iw), (xs[1] - ox) / scale)
    y1 = min(float(ih), (ys[1] - oy) / scale)
    px = max(0, min(round(x0), iw - 1))
    py = max(0, min(round(y0), ih - 1))
    pw = min(round(x1 - x0), iw - px)
    ph = min(round(y1 - y0), ih - py)
    if pw <= 0 or ph <= 0:
        return None
    return px, py, pw, ph
