"""Mede os frames de um vídeo .Mjpeg do ProgEx e quantos fragmentos IP cada um gera.

    python3 scripts/medir_frames.py movie.Mjpeg [--perda 0.2]

Formato do ficheiro (VideoStream.py): cada frame vem precedido de 5 bytes ASCII com o seu tamanho.
Cada frame vai num pacote: IP (20) + UDP (8) + cabeçalho overlay (14) + RTP (12) + frame.
Se passar do MTU (1500 B), o IP parte-o em fragmentos e basta perder um para perder o frame todo.
"""
from __future__ import annotations

import argparse
import math
import statistics
from collections import Counter

MTU = 1500
IP, UDP, OVERLAY, RTP = 20, 8, 14, 12
# Cada fragmento leva o seu cabeçalho IP; o resto (UDP + overlay + RTP + frame) é dividido
# em blocos de dados múltiplos de 8 bytes.
DADOS_POR_FRAGMENTO = (MTU - IP) // 8 * 8  # 1480


def ler_frames(caminho: str) -> list[int]:
    tamanhos = []
    with open(caminho, "rb") as f:
        while cab := f.read(5):
            n = int(cab)
            if len(f.read(n)) != n:
                raise ValueError(f"frame {len(tamanhos) + 1} cortado a meio")
            tamanhos.append(n)
    return tamanhos


def fragmentos(frame: int) -> int:
    return math.ceil((UDP + OVERLAY + RTP + frame) / DADOS_POR_FRAGMENTO)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("video")
    p.add_argument("--perda", type=float, action="append",
                   help="perda por pacote (0-1) para estimar a perda de frames; pode repetir")
    a = p.parse_args()

    tam = ler_frames(a.video)
    frags = [fragmentos(t) for t in tam]
    limite = MTU - IP - UDP - OVERLAY - RTP

    print(f"{a.video}: {len(tam)} frames")
    print(f"tamanho do frame: mín {min(tam)} B, média {statistics.mean(tam):.0f} B, "
          f"mediana {statistics.median(tam):.0f} B, máx {max(tam)} B")
    acima = sum(t > limite for t in tam)
    print(f"cabe num só pacote se frame <= {limite} B (MTU {MTU} - IP {IP} - UDP {UDP} "
          f"- overlay {OVERLAY} - RTP {RTP})")
    print(f"frames que são fragmentados: {acima} de {len(tam)} ({100 * acima / len(tam):.1f} %)")
    print("fragmentos por frame:")
    for k, n in sorted(Counter(frags).items()):
        print(f"  {k}: {n} frames")
    print(f"média: {statistics.mean(frags):.2f} fragmentos/frame")

    for perda in a.perda or [0.05, 0.1, 0.2, 0.3]:
        # Um frame chega só se todos os fragmentos chegarem.
        perdidos = statistics.mean(1 - (1 - perda) ** k for k in frags)
        print(f"perda {100 * perda:.0f} % por pacote -> ~{100 * perdidos:.1f} % dos frames perdidos")


if __name__ == "__main__":
    main()
