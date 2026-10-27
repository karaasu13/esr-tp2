"""Resume os logs da demo (logs/<nó>-<teste>.log): o que cada nó recebeu de cada vizinho,
mensagens em falta ou repetidas, retransmissões, RTT e eventos de vizinhos.

    python3 scripts/analisar_logs.py logs/O1-perda20.log logs/C1-perda20.log
"""
from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict

RX = re.compile(r"^(\S+) \[(\d+)\] <- (\d+): HELLO \{'demo': (\d+)\}(?:\s+\(RTT ([\d.]+) ms\))?")
RETX = re.compile(r"^(\S+) \[(\d+)\] retransmissão (\d+) de \S+ \(msg (\d+)\) -> (\d+),")
EVENTO = re.compile(r"^(\S+) \[(\d+)\] (vizinho .*|.*sem ACK.*)$")


def analisar(caminho: str) -> None:
    # Uma lista de números por "vida" do vizinho: quando ele volta (reiniciou), a demo dele
    # recomeça em 1, e isso não são repetições.
    rx: dict[str, list[list[int]]] = defaultdict(lambda: [[]])
    rtt: dict[str, list[float]] = defaultdict(list)
    retx: dict[str, int] = defaultdict(int)
    msgs_retx: dict[str, set[str]] = defaultdict(set)
    eventos: list[str] = []
    no = "?"
    for linha in open(caminho, encoding="utf-8", errors="replace"):
        linha = linha.rstrip()
        if m := RX.match(linha):
            no = m[2]
            rx[m[3]][-1].append(int(m[4]))
            if m[5]:
                rtt[m[3]].append(float(m[5]))
        elif m := RETX.match(linha):
            no = m[2]
            retx[m[5]] += 1
            msgs_retx[m[5]].add(m[4])
        elif m := EVENTO.match(linha):
            eventos.append(f"{m[1]}  {m[3]}")
            if v := re.match(r"vizinho (\d+) de volta", m[3]):
                rx[v[1]].append([])

    print(f"\n=== {caminho} (nó {no}) ===")
    print(f"{'vizinho':>7} | {'recebidas':>9} | {'únicas':>6} | {'em falta':>8} | {'fora de ordem':>13} | {'RTT min/mediana/máx (ms)':>24}")
    for viz in sorted(rx, key=int):
        vidas = [v for v in rx[viz] if v]
        ns = [n for v in vidas for n in v]
        if not ns:
            continue
        unicas = sum(len(set(v)) for v in vidas)
        falta = sum(len(set(range(min(v), max(v) + 1)) - set(v)) for v in vidas)
        fora = sum(1 for v in vidas for a, b in zip(v, v[1:]) if b < a)
        r = rtt[viz]
        r_txt = f"{min(r):.1f} / {statistics.median(r):.1f} / {max(r):.1f}" if r else "-"
        print(f"{viz:>7} | {len(ns):>9} | {unicas:>6} | {falta:>8} | {fora:>13} | {r_txt:>24}"
              + (f"   ({len(vidas)} períodos: o vizinho caiu e voltou)" if len(vidas) > 1 else ""))
    if retx:
        print("retransmissões feitas por este nó:")
        for viz in sorted(retx, key=int):
            print(f"  -> {viz}: {retx[viz]} retransmissões, em {len(msgs_retx[viz])} mensagens")
    if eventos:
        print("eventos:")
        for e in eventos:
            print("  " + e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for caminho in sys.argv[1:]:
        analisar(caminho)
