"""Escolha de caminhos: anúncios dos servidores e pai de cada servidor (versão mínima).

Passos 1 e 2 da secção 14 de docs/desenho-rotas-e-video.md:
  * o nó com servidor local envia ANNOUNCE a todos os vizinhos de ~2 em 2 s;
  * cada nó guarda o último anúncio de cada vizinho, por servidor, e fica com o vizinho
    mais barato como pai;
  * o anúncio de um vizinho vale VALIDADE_ANUNCIO; sem renovação é apagado, e sem anúncios
    a rota para o servidor desaparece;
  * só troca de pai se o pai cair ou o anúncio dele expirar: passa logo ao mais barato dos
    vizinhos com anúncio da ronda mais recente;
  * quando a rota muda, anuncia-a a todos os vizinhos menos ao pai.
Ainda não há margem para trocar, regra anti-ciclo nem WITHDRAW.
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
VALIDADE_ANUNCIO = 6.0  # segundos (3 períodos) até o anúncio de um vizinho ser apagado
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
    recebido: float = field(default_factory=time.monotonic)

    @property
    def custo(self) -> float:
        return self.rtt + self.saltos  # = custo anunciado + (RTT da ligação em ms + 1)


@dataclass
class RotaServidor:
    anuncios: dict[int, Anuncio] = field(default_factory=dict)  # vizinho -> último anúncio
    pai: int | None = None
    ronda: int = 0  # ronda mais nova já anunciada; sem pai, só serve um anúncio mais novo


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
            # Sem pai e sem alternativa quando o anterior caiu: espera por uma ronda mais nova.
            candidatos = [v for v, a in rota.anuncios.items() if a.ronda > rota.ronda]
            if candidatos:
                self._escolher_pai(srv, rota, candidatos, "")
        elif origem == rota.pai:
            self._anunciar(srv, rota)
        # anúncio de outro vizinho: fica guardado, sem troca por um vizinho melhor nesta versão

    def vizinho_em_baixo(self, no: int) -> None:
        """Esquece os anúncios do vizinho; se era o pai, passa logo a outro."""
        for srv, rota in list(self.servidores.items()):
            rota.anuncios.pop(no, None)
            if rota.pai == no:
                self._trocar_pai(srv, rota, f"vizinho {no} em baixo")

    def expirar(self) -> None:
        """Apaga os anúncios não renovados; sem anúncios, a rota para o servidor desaparece."""
        limite = time.monotonic() - VALIDADE_ANUNCIO
        for srv, rota in list(self.servidores.items()):
            velhos = [v for v, a in rota.anuncios.items() if a.recebido < limite]
            for vizinho in velhos:
                del rota.anuncios[vizinho]
            if rota.pai in velhos:
                self._trocar_pai(srv, rota, f"anúncio de {rota.pai} expirou")
            if not rota.anuncios:
                del self.servidores[srv]

    def _trocar_pai(self, srv: int, rota: RotaServidor, motivo: str) -> None:
        """O pai deixou de servir: fica o mais barato dos anúncios da ronda mais recente."""
        recente = max((a.ronda for a in rota.anuncios.values()), default=None)
        candidatos = [v for v, a in rota.anuncios.items() if a.ronda == recente]
        self._escolher_pai(srv, rota, candidatos, motivo)

    def _escolher_pai(self, srv: int, rota: RotaServidor, candidatos: list[int],
                      motivo: str) -> None:
        rota.pai = min(candidatos, key=lambda v: rota.anuncios[v].custo, default=None)
        if rota.pai is None:
            log.info("[%s] pai para o servidor %s: nenhum (%s)", self.transporte.id, srv, motivo)
        else:
            log.info("[%s] pai para o servidor %s: %s (custo %.1f)%s", self.transporte.id, srv,
                     rota.pai, rota.anuncios[rota.pai].custo, f" ({motivo})" if motivo else "")
        self.ao_mudar_pai(srv, rota.pai)
        if rota.pai is not None:
            self._anunciar(srv, rota)

    def _anunciar(self, srv: int, rota: RotaServidor) -> None:
        """Passa a rota aos vizinhos menos ao pai: a ronda do pai e o próprio custo."""
        escolhido = rota.anuncios[rota.pai]
        rota.ronda = max(rota.ronda, escolhido.ronda)  # a mais nova que já anunciou
        self._enviar({"srv": srv, "ronda": escolhido.ronda, "videos": escolhido.videos,
                      "rtt": escolhido.rtt, "saltos": escolhido.saltos}, exceto=rota.pai)

    def _enviar(self, corpo: dict, exceto: int | None) -> None:
        for vizinho in self.transporte.neighbors:
            if vizinho != exceto:
                self.transporte.send(vizinho, Tipo.ANNOUNCE, corpo)  # não fiável
