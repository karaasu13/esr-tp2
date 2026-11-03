#!/usr/bin/env python3
"""Regista este nó no bootstrapper e mostra o ID e os vizinhos recebidos."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ott.protocolo import PORTA, Tipo
from ott.transporte import Transporte


async def testar(ip_bootstrapper: str, porta: int, timeout: float) -> None:
    resposta: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

    def receber(origem, tipo, corpo, fluxo, endereco):
        del origem, fluxo, endereco
        if tipo == Tipo.REGISTER_ACK and not resposta.done():
            resposta.set_result(corpo)

    transporte = Transporte(on_message=receber)
    try:
        await transporte.start()
        transporte.set_address(0, (ip_bootstrapper, porta))
        transporte.register()
        try:
            corpo = await asyncio.wait_for(resposta, timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"sem resposta do bootstrapper {ip_bootstrapper}:{porta} "
                f"após {timeout:g} segundos"
            ) from exc

        if not isinstance(corpo, dict) or not isinstance(corpo.get("id"), int):
            raise RuntimeError(f"REGISTER_ACK inválido: {corpo!r}")
        vizinhos = corpo.get("vizinhos")
        if not isinstance(vizinhos, list):
            raise RuntimeError(f"REGISTER_ACK sem lista de vizinhos: {corpo!r}")

        print(f"Registo aceite. ID atribuído: {corpo['id']}")
        print(f"Vizinhos ({len(vizinhos)}):")
        for vizinho in vizinhos:
            print(f"  ID {vizinho['id']} — {vizinho['ip']}")
    finally:
        transporte.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip_bootstrapper", help="IP do nó onde corre o bootstrapper")
    parser.add_argument("--porta", type=int, default=PORTA, help=f"porta UDP (predefinição: {PORTA})")
    parser.add_argument("--timeout", type=float, default=15, help="tempo máximo de espera em segundos")
    args = parser.parse_args()
    try:
        asyncio.run(testar(args.ip_bootstrapper, args.porta, args.timeout))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    main()
