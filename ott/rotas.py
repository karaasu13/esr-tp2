"""Escolha de caminhos: anúncios dos servidores e pai de cada servidor (versão mínima).

Passo 1 da secção 14 de docs/desenho-rotas-e-video.md:
  * o nó com servidor local envia ANNOUNCE a todos os vizinhos de ~2 em 2 s;
  * cada nó guarda o último anúncio de cada vizinho, por servidor, e fica com o vizinho
    mais barato como pai;
  * só troca de pai se o pai cair; o novo pai é escolhido na ronda seguinte;
  * quando a rota muda, anuncia-a a todos os vizinhos menos ao pai.
Ainda não há margem para trocar, regra anti-ciclo, expiração de anúncios nem WITHDRAW.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .protocolo import Tipo
from .transporte import Transporte

log = logging.getLogger("ott.rotas")

PERIODO_ANUNCIO = 2.0  # segundos, com variação ao acaso de ±VARIACAO_ANUNCIO
VARIACAO_ANUNCIO = 0.1
MAX_SALTOS = 16  # rede de segurança contra ciclos
RTT_DESCONHECIDO_MS = 100.0  # custo de uma ligação ainda sem RTT medido

# Chamado com (servidor, novo pai ou None) sempre que o pai de um servidor muda.
AoMudarPai = Callable[[int, "int | None"], None]


@dataclass
class Anuncio:
    """Último anúncio de um vizinho, já somada a ligação até ele."""
    ronda: int
    videos: list[int]
    rtt: float  # ms, soma dos RTT do servidor até este nó, por este vizinho
    saltos: int

    @property
    def custo(self) -> float:
        return self.rtt + self.saltos  # = custo anunciado + (RTT da ligação em ms + 1)


@dataclass
class RotaServidor:
    anuncios: dict[int, Anuncio] = field(default_factory=dict)  # vizinho -> último anúncio
    pai: int | None = None
    ronda: int = 0  # ronda da rota atual; sem pai, só serve um anúncio mais novo do que esta


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


class Rotas:
    def __init__(self, transporte: Transporte, ao_mudar_pai: AoMudarPai):
        self.transporte = transporte
        self.ao_mudar_pai = ao_mudar_pai
        self.servidores: dict[int, RotaServidor] = {}

    def pai(self, servidor: int) -> int | None:
        rota = self.servidores.get(servidor)
        return rota.pai if rota else None

    async def anunciar_servidor(self, videos: list[int]) -> None:
        """Ciclo do nó com servidor local: anuncia-se a todos os vizinhos até ser cancelado."""
        ronda = int(time.time())  # um servidor que reinicia anuncia logo rondas mais altas
        while True:
            self._enviar({"srv": self.transporte.id, "ronda": ronda, "videos": videos,
                          "rtt": 0.0, "saltos": 0}, exceto=None)
            ronda += 1
            await asyncio.sleep(PERIODO_ANUNCIO * random.uniform(1 - VARIACAO_ANUNCIO,
                                                                 1 + VARIACAO_ANUNCIO))

    def receber(self, origem: int, corpo: dict) -> None:
        """ANNOUNCE de um vizinho confirmado."""
        srv, ronda, videos = corpo.get("srv"), corpo.get("ronda"), corpo.get("videos")
        rtt, saltos = corpo.get("rtt"), corpo.get("saltos")
        if not (_inteiro(srv) and 1 <= srv <= 65535 and _inteiro(ronda)
                and _inteiro(saltos) and saltos >= 0
                and isinstance(rtt, (int, float)) and not isinstance(rtt, bool) and rtt >= 0
                and isinstance(videos, list)
                and all(_inteiro(v) and 0 <= v <= 255 for v in videos)):
            log.warning("[%s] ANNOUNCE inválido de %s: %r", self.transporte.id, origem, corpo)
            return
        if srv == self.transporte.id or saltos > MAX_SALTOS:
            return
        rota = self.servidores.setdefault(srv, RotaServidor())
        anterior = rota.anuncios.get(origem)
        if anterior is not None and ronda < anterior.ronda:
            return
        rtt_ligacao = self.transporte.rtt(origem)
        ms = RTT_DESCONHECIDO_MS if rtt_ligacao is None else rtt_ligacao * 1000
        rota.anuncios[origem] = Anuncio(ronda, videos, rtt + ms, saltos + 1)

        if rota.pai is None:
            candidatos = [v for v, a in rota.anuncios.items() if a.ronda > rota.ronda]
            if not candidatos:
                return
            rota.pai = min(candidatos, key=lambda v: rota.anuncios[v].custo)
            log.info("[%s] pai para o servidor %s: %s (custo %.1f)", self.transporte.id,
                     srv, rota.pai, rota.anuncios[rota.pai].custo)
            self.ao_mudar_pai(srv, rota.pai)
        elif origem != rota.pai:
            return  # sem troca por um vizinho melhor nesta versão
        escolhido = rota.anuncios[rota.pai]
        rota.ronda = escolhido.ronda
        self._enviar({"srv": srv, "ronda": escolhido.ronda, "videos": escolhido.videos,
                      "rtt": escolhido.rtt, "saltos": escolhido.saltos}, exceto=rota.pai)

    def vizinho_em_baixo(self, no: int) -> None:
        """Esquece os anúncios do vizinho; se era o pai, o próximo sai da ronda seguinte."""
        for srv, rota in self.servidores.items():
            rota.anuncios.pop(no, None)
            if rota.pai == no:
                rota.pai = None
                log.info("[%s] pai para o servidor %s: nenhum (vizinho %s em baixo)",
                         self.transporte.id, srv, no)
                self.ao_mudar_pai(srv, None)

    def _enviar(self, corpo: dict, exceto: int | None) -> None:
        for vizinho in self.transporte.neighbors:
            if vizinho != exceto:
                self.transporte.send(vizinho, Tipo.ANNOUNCE, corpo)  # não fiável
