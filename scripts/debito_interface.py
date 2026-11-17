"""Mede o tráfego de uma interface (Linux, /proc/net/dev), para comparar com e sem overlay.

    python3 scripts/debito_interface.py eth0             # no S1: a cada segundo, até Ctrl+C
    python3 scripts/debito_interface.py eth0 --duracao 30

Mostra, a cada intervalo, os kbit/s e pacotes/s que saem (tx) e entram (rx), e no fim a média.
Conta tudo o que passa na interface: vídeo, controlo do overlay e o resto (ex: OSPF).
"""
from __future__ import annotations

import argparse
import time

PROC = "/proc/net/dev"


def contadores(texto: str, interface: str) -> tuple[int, int, int, int]:
    """(bytes rx, pacotes rx, bytes tx, pacotes tx) da interface, a partir do /proc/net/dev."""
    for linha in texto.splitlines():
        nome, sep, resto = linha.partition(":")
        if sep and nome.strip() == interface:
            campos = [int(x) for x in resto.split()]
            return campos[0], campos[1], campos[8], campos[9]
    raise ValueError(f"interface {interface} não encontrada em {PROC}")


def ler(interface: str) -> tuple[int, int, int, int]:
    with open(PROC, encoding="ascii") as f:
        return contadores(f.read(), interface)


def main() -> None:
    parser = argparse.ArgumentParser(description="Débito de uma interface (Linux)")
    parser.add_argument("interface", help="ex: eth0")
    parser.add_argument("--intervalo", type=float, default=1.0, help="segundos entre leituras (1)")
    parser.add_argument("--duracao", type=float, default=None, help="segundos até parar (Ctrl+C)")
    args = parser.parse_args()

    t0 = antes_t = time.monotonic()
    inicio = antes = ler(args.interface)
    print(f"{'s':>6} {'tx kbit/s':>10} {'tx pac/s':>9} {'rx kbit/s':>10} {'rx pac/s':>9}")
    try:
        while args.duracao is None or time.monotonic() - t0 < args.duracao:
            time.sleep(args.intervalo)
            agora_t, agora = time.monotonic(), ler(args.interface)
            dt = agora_t - antes_t
            rxb, rxp, txb, txp = (a - b for a, b in zip(agora, antes))
            print(f"{agora_t - t0:6.1f} {txb * 8 / dt / 1000:10.1f} {txp / dt:9.1f} "
                  f"{rxb * 8 / dt / 1000:10.1f} {rxp / dt:9.1f}", flush=True)
            antes_t, antes = agora_t, agora
    except KeyboardInterrupt:
        pass
    dt = antes_t - t0
    if dt > 0:
        rxb, rxp, txb, txp = (a - b for a, b in zip(antes, inicio))
        print(f"média em {dt:.1f} s: tx {txb * 8 / dt / 1000:.1f} kbit/s ({txp / dt:.1f} pac/s), "
              f"rx {rxb * 8 / dt / 1000:.1f} kbit/s ({rxp / dt:.1f} pac/s)")


if __name__ == "__main__":
    main()
