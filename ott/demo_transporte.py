"""Demo do transporte, para testar no CORE antes de haver bootstrapper.

Cada nó envia uma mensagem fiável a cada vizinho de 2 em 2 s e mostra o que
recebe, o RTT medido e os vizinhos que caem. Exemplo (cenário 1, nó O com ID 2):

    python3 -m ott.demo_transporte --id 2 1=10.0.0.10 3=10.0.1.20 4=10.0.2.20

Testes sugeridos no CORE:
  * perda no link (ex: 20%): aparecem retransmissões, mas cada nº chega 1 vez;
  * matar um nó: os vizinhos mostram "vizinho X em baixo" em ~3,5 s.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from .protocolo import PORTA, Tipo
from .transporte import Transporte


def vizinho(texto: str) -> tuple[int, tuple[str, int]]:
    no, _, resto = texto.partition("=")
    ip, _, porta = resto.partition(":")
    return int(no), (ip, int(porta or PORTA))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", type=int, required=True, help="ID deste nó")
    ap.add_argument("--porta", type=int, default=PORTA)
    ap.add_argument("vizinhos", nargs="+", type=vizinho, help="ID=IP[:porta]")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")

    log = logging.getLogger("ott.demo")
    config = dict(args.vizinhos)
    t: Transporte

    def recebida(origem, tipo, corpo, fluxo, addr):
        if origem in config and origem not in t.neighbors:
            # Um vizinho dado como em baixo voltou a dar sinal (ou arrancou depois de nós).
            t.add_neighbor(origem, config[origem])
            log.info("[%s] vizinho %s de volta", t.id, origem)
        rtt = t.rtt(origem)
        log.info("[%s] <- %s: %s %s%s", t.id, origem, Tipo(tipo).name, corpo,
                 f"  (RTT {rtt * 1000:.1f} ms)" if rtt else "")

    t = Transporte(args.id, on_message=recebida)
    await t.start(porta=args.porta)
    for no, addr in config.items():
        t.add_neighbor(no, addr)

    n = 0
    while True:
        n += 1
        for no in t.neighbors:
            t.send(no, Tipo.HELLO, {"demo": n}, fiavel=True)
        await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
