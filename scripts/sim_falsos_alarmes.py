"""Quantas vezes o idle timeout dá como morto um vizinho que está vivo, com perda?

Corre muitos pares de nós (o transporte real, em localhost) com perda simulada nos dois
sentidos e conta os falsos "vizinho em baixo". O tempo é acelerado: todos os
temporizadores são divididos por ESCALA, o que mantém as probabilidades (só contam as
razões entre keepalive, idle timeout e RTO) e o RTT em localhost (< 1 ms) continua
desprezável face aos temporizadores.

    python3 scripts/sim_falsos_alarmes.py [segundos_por_cenario] [keepalive] [idle_timeout]
"""
from __future__ import annotations

import asyncio
import logging
import sys

sys.path.insert(0, ".")
from ott.protocolo import Tipo  # noqa: E402
from ott.transporte import IDLE_TIMEOUT, KEEPALIVE, RTO_INICIAL, RTO_MAX, Transporte  # noqa: E402

ESCALA = 10
KA = float(sys.argv[2]) if len(sys.argv) > 2 else KEEPALIVE
IDLE = float(sys.argv[3]) if len(sys.argv) > 3 else IDLE_TIMEOUT
PARES = 20
PERDAS = (0.1, 0.2, 0.3, 0.4)
INTERVALO_DEMO = 2.0  # como a demo: uma mensagem fiável a cada vizinho de 2 em 2 s


async def cenario(perda: float, com_trafego: bool, duracao: float, semente: int) -> tuple[int, float]:
    falsos = 0
    nos: list[Transporte] = []
    kw = dict(keepalive=KA / ESCALA, idle_timeout=IDLE / ESCALA,
              rto_inicial=RTO_INICIAL / ESCALA, rto_max=RTO_MAX / ESCALA, perda=perda)

    def on_down(t: Transporte, outro: int, addr):
        nonlocal falsos
        falsos += 1  # os dois nós estão sempre vivos: qualquer "em baixo" é falso
        t.add_neighbor(outro, addr)  # volta a aceitá-lo, como a demo faz ao receber algo

    for i in range(PARES):
        a = Transporte(1, perda=perda, seed=semente + 2 * i, **{k: v for k, v in kw.items() if k != "perda"})
        b = Transporte(2, perda=perda, seed=semente + 2 * i + 1, **{k: v for k, v in kw.items() if k != "perda"})
        await a.start("127.0.0.1", 0)
        await b.start("127.0.0.1", 0)
        addr_a, addr_b = a.endereco_local, b.endereco_local
        a.on_neighbor_down = lambda n, t=a, x=addr_b: on_down(t, n, x)
        b.on_neighbor_down = lambda n, t=b, x=addr_a: on_down(t, n, x)
        a.add_neighbor(2, addr_b)
        b.add_neighbor(1, addr_a)
        nos += [a, b]

    async def trafego():
        while True:
            for t in nos:
                for v in t.neighbors:
                    t.send(v, Tipo.HELLO, {"x": 1}, fiavel=True)
            await asyncio.sleep(INTERVALO_DEMO / ESCALA)

    tarefa = asyncio.create_task(trafego()) if com_trafego else None
    await asyncio.sleep(duracao)
    if tarefa:
        tarefa.cancel()
    for t in nos:
        t.close()
    horas_vizinho = len(nos) * duracao * ESCALA / 3600  # cada nó vigia 1 vizinho
    return falsos, horas_vizinho


async def main() -> None:
    logging.getLogger("ott.transporte").setLevel(logging.ERROR)
    duracao = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    print(f"keepalive {KA} s, idle timeout {IDLE} s; {PARES} pares; "
          f"{duracao:.0f} s reais por cenário = {duracao * ESCALA / 60:.0f} min de protocolo por par")
    print("perda/sentido | tráfego | falsos 'em baixo' | horas de vigia | por hora de vizinho")
    for perda in PERDAS:
        for com_trafego in (False, True):
            f, h = await cenario(perda, com_trafego, duracao, semente=int(perda * 1000) + com_trafego)
            print(f"{perda:>12.0%} | {'demo' if com_trafego else 'nenhum':>7} | {f:>17} | {h:>14.1f} | {f / h:>8.3f}")


if __name__ == "__main__":
    asyncio.run(main())
