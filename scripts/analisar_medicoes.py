"""Resume os CSV do medidor (player/Medidor.py): perda, atraso, jitter, débito e cortes no vídeo.

    python3 scripts/analisar_medicoes.py logs/C1-overlay.csv logs/C1-direto.csv
    python3 scripts/analisar_medicoes.py logs/C1-falha.csv --corte 300

Medidas (uma linha do CSV por frame recebido):
- perda: frames entre o 1.º e o último nº de sequência que nunca chegaram;
- atraso: hora de chegada menos hora de envio (o timestamp RTP, em ms). No CORE todos os nós
  partilham o relógio, por isso é o atraso de ponta a ponta;
- jitter: o estimador do RFC 3550 (secção 6.4.1), J += (|D| - J) / 16, pela ordem de chegada;
- débito: bytes de vídeo (só o frame JPEG) por segundo, entre o 1.º e o último frame;
- 1.º frame: quanto tempo depois de o medidor arrancar chegou o 1.º frame;
- cortes: intervalos sem frames maiores do que --corte ms (por omissão 300, 6 frames a 20 por
  segundo). Numa falha, a duração do corte é o tempo que o vídeo esteve parado.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass, field

INICIO = "# medidor iniciado:"
SEQ_MOD = 1 << 16


@dataclass
class Frame:
    chegada_ms: float
    seq: int
    envio_ms: int
    atraso_ms: float
    bytes: int


@dataclass
class Resumo:
    recebidos: int = 0
    esperados: int = 0
    perdidos: int = 0
    duplicados: int = 0
    fora_de_ordem: int = 0
    atraso_medio: float = 0.0
    atraso_mediana: float = 0.0
    atraso_p95: float = 0.0
    atraso_max: float = 0.0
    jitter: float = 0.0
    duracao_s: float = 0.0
    fps: float = 0.0
    kbit_s: float = 0.0
    primeiro_frame_s: float | None = None
    cortes: list[tuple[float, float]] = field(default_factory=list)  # (início em s, duração em ms)

    @property
    def perda(self) -> float:
        return 100 * self.perdidos / self.esperados if self.esperados else 0.0


def ler(caminho: str) -> tuple[float | None, list[Frame]]:
    """Devolve a hora de arranque do medidor (ms, ou None) e os frames pela ordem de chegada."""
    inicio = None
    linhas = []
    with open(caminho, newline="", encoding="utf-8") as f:
        for linha in f:
            if linha.startswith(INICIO):
                inicio = float(linha[len(INICIO):])
            elif not linha.startswith("#"):
                linhas.append(linha)
    frames = [
        Frame(float(r["chegada_ms"]), int(r["seq"]), int(r["envio_ms"]),
              float(r["atraso_ms"]), int(r["bytes"]))
        for r in csv.DictReader(linhas)
    ]
    return inicio, frames


def percentil(valores: list[float], p: float) -> float:
    """Percentil p (0-100) pelo método do vizinho mais próximo (valores ordenados)."""
    if not valores:
        return 0.0
    k = max(0, min(len(valores) - 1, math.ceil(p / 100 * len(valores)) - 1))
    return valores[k]


def resumir(frames: list[Frame], inicio_ms: float | None = None, corte_ms: float = 300) -> Resumo:
    r = Resumo()
    if not frames:
        return r
    # Nº de sequência sem a volta dos 16 bits, pela ordem de chegada.
    estendidos = []
    ultimo = None
    for fr in frames:
        if ultimo is None:
            ext = fr.seq
        else:
            dif = (fr.seq - ultimo % SEQ_MOD) % SEQ_MOD
            ext = ultimo + (dif if dif < SEQ_MOD // 2 else dif - SEQ_MOD)
        estendidos.append(ext)
        ultimo = ext if ultimo is None else max(ultimo, ext)
    vistos = set()
    maior = None
    for ext in estendidos:
        if ext in vistos:
            r.duplicados += 1
            continue
        if maior is not None and ext < maior:
            r.fora_de_ordem += 1
        vistos.add(ext)
        maior = ext if maior is None else max(maior, ext)
    r.recebidos = len(vistos)
    r.esperados = max(vistos) - min(vistos) + 1
    r.perdidos = r.esperados - r.recebidos

    atrasos = sorted(fr.atraso_ms for fr in frames)
    r.atraso_medio = statistics.fmean(atrasos)
    r.atraso_mediana = statistics.median(atrasos)
    r.atraso_p95 = percentil(atrasos, 95)
    r.atraso_max = atrasos[-1]

    for antes, depois in zip(frames, frames[1:]):
        d = (depois.chegada_ms - antes.chegada_ms) - (depois.envio_ms - antes.envio_ms)
        r.jitter += (abs(d) - r.jitter) / 16
        intervalo = depois.chegada_ms - antes.chegada_ms
        if intervalo > corte_ms:
            base = inicio_ms if inicio_ms is not None else frames[0].chegada_ms
            r.cortes.append(((antes.chegada_ms - base) / 1000, intervalo))

    r.duracao_s = (frames[-1].chegada_ms - frames[0].chegada_ms) / 1000
    if r.duracao_s > 0:
        r.fps = (len(frames) - 1) / r.duracao_s
        r.kbit_s = sum(fr.bytes for fr in frames[1:]) * 8 / r.duracao_s / 1000
    if inicio_ms is not None:
        r.primeiro_frame_s = (frames[0].chegada_ms - inicio_ms) / 1000
    return r


def mostrar(caminho: str, r: Resumo) -> None:
    print(f"== {caminho}")
    if not r.recebidos:
        print("   sem frames")
        return
    print(f"   frames: {r.recebidos} recebidos de {r.esperados} · perdidos {r.perdidos} "
          f"({r.perda:.2f} %) · repetidos {r.duplicados} · fora de ordem {r.fora_de_ordem}")
    print(f"   atraso (ms): médio {r.atraso_medio:.2f} · mediana {r.atraso_mediana:.2f} · "
          f"p95 {r.atraso_p95:.2f} · máx {r.atraso_max:.2f}")
    print(f"   jitter (RFC 3550): {r.jitter:.2f} ms")
    print(f"   débito: {r.kbit_s:.0f} kbit/s · {r.fps:.1f} frames/s durante {r.duracao_s:.1f} s")
    if r.primeiro_frame_s is not None:
        print(f"   1.º frame: {r.primeiro_frame_s:.2f} s depois de o medidor arrancar")
    if r.cortes:
        print(f"   cortes: {len(r.cortes)} (maior {max(c[1] for c in r.cortes):.0f} ms)")
        for t, dur in r.cortes:
            print(f"     aos {t:.1f} s: {dur:.0f} ms sem vídeo")
    else:
        print("   cortes: nenhum")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume os CSV do medidor (player/Medidor.py)")
    parser.add_argument("csv", nargs="+", help="ficheiros gravados pelo medidor")
    parser.add_argument("--corte", type=float, default=300,
                        help="ms sem frames que contam como corte (predefinição: 300)")
    args = parser.parse_args()
    for caminho in args.csv:
        inicio, frames = ler(caminho)
        mostrar(caminho, resumir(frames, inicio, args.corte))


if __name__ == "__main__":
    main()
