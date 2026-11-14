"""Escolha de caminhos: anúncios dos servidores e pai de cada servidor.

Secção 14 de docs/desenho-rotas-e-video.md:
  * o nó com servidor local envia ANNOUNCE a todos os vizinhos de ~2 em 2 s;
  * cada nó guarda o último anúncio de cada vizinho, por servidor, já somadas as medidas da
    ligação (RTT mínimo, jitter, perda e 1 salto), e fica com o vizinho mais barato como pai;
  * o anúncio de um vizinho vale VALIDADE_ANUNCIO; sem renovação é apagado, e sem anúncios
    a rota para o servidor desaparece;
  * se o pai cair, o anúncio dele expirar ou ele enviar WITHDRAW, passa logo ao mais barato dos
    vizinhos viáveis; se não houver nenhum, envia WITHDRAW a todos os vizinhos e fica sem pai
    até chegar uma ronda mais nova;
  * se o pai deixar de enviar vídeo (300 ms num fluxo ativo), passa ao melhor vizinho viável;
  * troca por um vizinho viável claramente melhor (margem de 15% e 2 unidades), comparando
    anúncios da mesma ronda;
  * quando a rota muda, anuncia-a a todos os vizinhos menos ao pai.

Regra anti-ciclo (condição de viabilidade do Babel e do EIGRP), aplicada a TODAS as escolhas de
pai: um vizinho só pode ser pai se o anúncio dele for de uma ronda mais nova do que a última que
este nó anunciou, ou da mesma ronda com um custo anunciado menor do que o menor custo que este nó
anunciou nessa ronda. Quem recebe através deste nó só conhece rondas que este nó já anunciou, e
anuncia sempre mais (soma pelo menos uma ligação e mais 1 salto), por isso nunca é viável.
"""
from __future__ import annotations

import asyncio
import logging
import math
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
RTT_DESCONHECIDO_MS = 100.0  # RTT de uma ligação ainda sem medições
MARGEM_RELATIVA = 0.15  # só troca por um vizinho melhor se ganhar mais de 15%...
MARGEM_ABSOLUTA = 2.0  # ...e mais de 2 unidades de custo

# Chamado com (servidor, novo pai ou None) sempre que o pai de um servidor muda.
AoMudarPai = Callable[[int, "int | None"], None]


def custo(rtt_ms: float, jitter_ms: float, perda_pct: float, saltos: int) -> float:
    """A fórmula do custo (secção 6 do desenho). Serve para uma ligação e para um caminho
    inteiro, porque é uma soma: o custo do caminho é a soma dos custos das ligações."""
    return rtt_ms + jitter_ms + 20 * perda_pct + saltos


@dataclass
class Anuncio:
    """Último anúncio de um vizinho, já somada a ligação até ele."""
    ronda: int
    videos: list[int]
    rtt: float  # ms: soma dos RTT mínimos do servidor até este nó, por este vizinho
    jitter: float  # ms, somado da mesma forma
    perda: float  # %, somada da mesma forma
    saltos: int
    anunciado: float  # o custo que o vizinho anunciou, para a regra anti-ciclo
    recebido: float = field(default_factory=time.monotonic)

    @property
    def custo(self) -> float:
        return custo(self.rtt, self.jitter, self.perda, self.saltos)


@dataclass
class RotaServidor:
    anuncios: dict[int, Anuncio] = field(default_factory=dict)  # vizinho -> último anúncio
    pai: int | None = None
    ronda: int = 0  # ronda mais nova que este nó já anunciou
    custo_anunciado: float = math.inf  # menor custo que este nó anunciou nessa ronda
    # Último anúncio guardado. Uma rota vazia por expiração é apagada logo; uma que ficou vazia
    # por WITHDRAW ou vizinho em baixo só VALIDADE_ANUNCIO depois disto, para a ronda e o custo
    # acima continuarem a valer contra anúncios antigos que ainda andem pela rede.
    atualizada: float = field(default_factory=time.monotonic)

    def viavel(self, anuncio: "Anuncio") -> bool:
        """Pode ser pai sem risco de ciclo (ver a regra no início do módulo)."""
        return anuncio.ronda > self.ronda or (
            anuncio.ronda == self.ronda and anuncio.anunciado < self.custo_anunciado)


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _medida(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and 0 <= valor < math.inf


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
                          "rtt": 0.0, "jitter": 0.0, "perda": 0.0, "saltos": 0}, exceto=None)
            ronda += 1
            await asyncio.sleep(PERIODO_ANUNCIO * random.uniform(1 - VARIACAO_ANUNCIO,
                                                                 1 + VARIACAO_ANUNCIO))

    def receber(self, origem: int, corpo: dict) -> None:
        """ANNOUNCE de um vizinho confirmado."""
        srv, ronda, videos = corpo.get("srv"), corpo.get("ronda"), corpo.get("videos")
        rtt, saltos = corpo.get("rtt"), corpo.get("saltos")
        jitter, perda = corpo.get("jitter", 0.0), corpo.get("perda", 0.0)  # anúncios antigos: 0
        if not (_inteiro(srv) and 1 <= srv <= 65535 and _inteiro(ronda)
                and _inteiro(saltos) and saltos >= 0
                and _medida(rtt) and _medida(jitter) and _medida(perda)
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
        rtt_l, jitter_l, perda_l = self._medidas_ligacao(origem)
        rota.atualizada = time.monotonic()
        rota.anuncios[origem] = Anuncio(
            ronda, videos, rtt + rtt_l, jitter + jitter_l, perda + perda_l, saltos + 1,
            anunciado=custo(rtt, jitter, perda, saltos))

        if rota.pai is None:
            # Sem pai: só serve um vizinho viável (em geral, um que já tenha uma ronda mais nova).
            candidatos = [v for v, a in rota.anuncios.items() if rota.viavel(a)]
            if candidatos:
                self._escolher_pai(srv, rota, candidatos, "")
        elif not self._trocar_se_melhor(srv, rota) and origem == rota.pai:
            self._anunciar(srv, rota)

    def _medidas_ligacao(self, vizinho: int) -> tuple[float, float, float]:
        """(RTT mínimo em ms, jitter em ms, perda em %) da ligação; sem medições, 100/0/0."""
        rtt, jitter, perda = (self.transporte.rtt_min(vizinho), self.transporte.jitter(vizinho),
                              self.transporte.perda(vizinho))
        return (RTT_DESCONHECIDO_MS if rtt is None else rtt * 1000,
                0.0 if jitter is None else jitter * 1000,
                0.0 if perda is None else perda * 100)

    def _trocar_se_melhor(self, srv: int, rota: RotaServidor) -> bool:
        """Troca por um vizinho viável claramente mais barato, na mesma ronda do pai."""
        atual = rota.anuncios[rota.pai]
        melhores = [v for v, a in rota.anuncios.items()
                    if v != rota.pai and a.ronda == atual.ronda and rota.viavel(a)
                    and a.custo < atual.custo * (1 - MARGEM_RELATIVA)
                    and atual.custo - a.custo > MARGEM_ABSOLUTA]
        if not melhores:
            return False
        antigo = rota.pai
        self._escolher_pai(srv, rota, melhores, f"melhor do que {antigo}, custo {atual.custo:.1f}")
        return True

    def receber_withdraw(self, origem: int, corpo: dict) -> None:
        """WITHDRAW de um vizinho confirmado: perdeu a rota para o servidor."""
        srv = corpo.get("srv")
        if not (_inteiro(srv) and 1 <= srv <= 65535):
            log.warning("[%s] WITHDRAW inválido de %s: %r", self.transporte.id, origem, corpo)
            return
        rota = self.servidores.get(srv)
        if rota is None:
            return
        rota.anuncios.pop(origem, None)
        if rota.pai == origem:
            self._trocar_pai(srv, rota, f"WITHDRAW de {origem}")

    def pai_sem_video(self, srv: int, segundos: float) -> None:
        """O pai deixou de enviar vídeo: passa ao melhor vizinho viável, se houver. Não dá o pai
        como em baixo; sem alternativa viável, fica com ele."""
        rota = self.servidores.get(srv)
        if rota is None or rota.pai is None:
            return
        antigo = rota.pai
        candidatos = [v for v, a in rota.anuncios.items() if v != antigo and rota.viavel(a)]
        if candidatos:
            del rota.anuncios[antigo]  # como na queda: volta a contar com o próximo anúncio dele
            self._escolher_pai(srv, rota, candidatos,
                               f"{segundos * 1000:.0f} ms sem vídeo de {antigo}")

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
            if not rota.anuncios and (velhos or rota.atualizada < limite):
                del self.servidores[srv]

    def _trocar_pai(self, srv: int, rota: RotaServidor, motivo: str) -> None:
        """O pai deixou de servir: fica o mais barato dos vizinhos viáveis, se houver."""
        candidatos = [v for v, a in rota.anuncios.items() if rota.viavel(a)]
        self._escolher_pai(srv, rota, candidatos, motivo)
        if rota.pai is None:  # sem reserva: quem recebe através deste nó procura já outro pai
            for vizinho in self.transporte.neighbors:
                self.transporte.send(vizinho, Tipo.WITHDRAW, {"srv": srv}, fiavel=True)

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
        if escolhido.ronda > rota.ronda:  # guarda o que anunciou, para a regra anti-ciclo
            rota.ronda, rota.custo_anunciado = escolhido.ronda, escolhido.custo
        elif escolhido.ronda == rota.ronda:
            rota.custo_anunciado = min(rota.custo_anunciado, escolhido.custo)
        self._enviar({"srv": srv, "ronda": escolhido.ronda, "videos": escolhido.videos,
                      "rtt": escolhido.rtt, "jitter": escolhido.jitter, "perda": escolhido.perda,
                      "saltos": escolhido.saltos}, exceto=rota.pai)

    def _enviar(self, corpo: dict, exceto: int | None) -> None:
        for vizinho in self.transporte.neighbors:
            if vizinho != exceto:
                self.transporte.send(vizinho, Tipo.ANNOUNCE, corpo)  # não fiável
