"""Transporte "QUIC-like" sobre UDP: um socket por nó, para todos os vizinhos.

O que este módulo garante a quem o usa (bootstrapper, vizinhos, rotas, streaming):
  * send(..., fiavel=True): a mensagem chega uma e uma só vez, ou o vizinho é dado
    como morto. ACK e retransmissão são tratados aqui, não no código de cima.
    Para quem não é vizinho (ex: bootstrapper) desiste-se ao fim de MAX_TENTATIVAS.
  * send(..., fiavel=False): melhor esforço (vídeo, mensagens periódicas).
  * send(..., sonda=True): pede ACK mas não é retransmitido. É assim que o nó envia o HELLO
    periódico: os ACK dão o RTT e a perda, e o HELLO seguinte faz o papel de retransmissão.
  * on_neighbor_down(id): chamado quando um vizinho deixa de dar sinal. O limite de silêncio é
    3 vezes o intervalo de HELLO que o vizinho anunciou (set_intervalo), e nunca menos do que
    3 vezes o tempo de retransmissão (RTO) para ele: numa ligação lenta estica sozinho.
    Mensagens fiáveis para um vizinho são retransmitidas até haver ACK ou até ele cair.
  * rtt(id): RTT suavizado até ao vizinho, medido com os ACK (métrica da Etapa 2).
  * rtt_min(id), jitter(id), perda(id): as três medidas da ligação que as rotas usam no custo.

Ideias do QUIC usadas (ver docs/protocolo.md):
  * nº de pacote nunca reutilizado, nem nas retransmissões -> RTT sem ambiguidade;
  * a identidade da mensagem (id_msg) é separada do nº de pacote -> o recetor
    deteta duplicados quando um ACK se perde e a mensagem é retransmitida;
  * idle timeout para detetar vizinhos mortos, com o limite combinado entre vizinhos como no
    BFD (RFC 5880) e nunca abaixo de 3 x RTO, como a regra do QUIC (RFC 9000).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

from .protocolo import (
    BOOTSTRAPPER, CORPO_ACK, FLUXO_CONTROLO, PORTA, SEM_ID, Flag, Pacote, Tipo,
    codificar_corpo, descodificar_corpo, nome_tipo,
)

log = logging.getLogger("ott.transporte")

Endereco = tuple[str, int]
OnMessage = Callable[[int, int, "dict | bytes", tuple[int, int], Endereco], None]
OnNeighborDown = Callable[[int], None]

# Valores por omissão dos temporizadores (segundos)
INTERVALO_HELLO = 1.0  # intervalo de HELLO assumido para um vizinho até ele anunciar o seu
MULTIPLICADOR = 3  # em baixo ao fim de 3 intervalos sem nada dele (o valor habitual no BFD)
RTO_INICIAL = 0.5  # timeout de retransmissão antes de haver medições de RTT
RTO_MIN = 0.2
RTO_MAX = 2.0  # teto do backoff, para não ficar minutos à espera
MAX_TENTATIVAS = 5  # para não vizinhos; para vizinhos só avisa e continua (decide o idle timeout)

MAX_ENVIOS_GUARDADOS = 4096  # para o cálculo do RTT
AMOSTRAS_RTT_MIN = 10  # rtt_min = mínimo das últimas N amostras
AMOSTRAS_PERDA = 20  # perda = fração das últimas N sondas (HELLO) sem ACK
ESPERA_ACK_SONDA = 1.0  # uma sonda sem ACK neste tempo conta como perdida
PASSO_VIGIA = 0.05  # de quanto em quanto tempo se verificam os limites de silêncio
MAX_VISTOS_POR_ORIGEM = 1024  # para a deteção de duplicados


@dataclass
class _Pendente:
    """Mensagem fiável à espera de ACK."""
    destino: int
    addr: Endereco
    tipo: int
    corpo: bytes
    fluxo: tuple[int, int]
    id_msg: int
    tentativas: int = 0
    pns: list[int] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None


@dataclass
class _Vizinho:
    addr: Endereco
    ultimo_rx: float
    intervalo: float = INTERVALO_HELLO  # intervalo de HELLO que o vizinho anunciou
    sondas: deque = field(default_factory=lambda: deque(maxlen=AMOSTRAS_PERDA))  # True = com ACK


class Transporte(asyncio.DatagramProtocol):
    def __init__(
        self,
        meu_id: int = SEM_ID,
        on_message: OnMessage | None = None,
        on_neighbor_down: OnNeighborDown | None = None,
        *,
        idle_timeout: float | None = None,
        rto_inicial: float = RTO_INICIAL,
        rto_max: float = RTO_MAX,
        max_tentativas: int = MAX_TENTATIVAS,
        perda: float = 0.0,
        seed: int | None = None,
    ):
        self.id = meu_id
        self.on_message = on_message
        self.on_neighbor_down = on_neighbor_down
        # None: limite de silêncio combinado com cada vizinho (ver limite()); um número fixa-o
        # para todos (útil nos testes, para não esperar 3 s).
        self.idle_timeout = idle_timeout
        self.rto_inicial = rto_inicial
        self.rto_max = rto_max
        self.max_tentativas = max_tentativas
        self.perda_simulada = perda  # perda simulada no envio, só para testes fora do CORE
        self._rand = random.Random(seed)

        self._transport: asyncio.DatagramTransport | None = None
        self._tarefa_vigia: asyncio.Task | None = None
        # Nº inicial aleatório, como o ISN do TCP: se o nó reiniciar, os ids das mensagens
        # novas não colidem com os que os vizinhos ainda guardam da execução anterior.
        self._pn = self._rand.randrange(1, 1 << 31)
        self._enderecos: dict[int, Endereco] = {}  # ID -> (ip, porta), inclui o bootstrapper
        self._vizinhos: dict[int, _Vizinho] = {}  # só estes têm limite de silêncio e medidas
        self._pendentes: dict[int, _Pendente] = {}  # id_msg -> mensagem por confirmar
        self._pn_para_msg: dict[int, int] = {}  # pn -> id_msg (cada retransmissão tem pn novo)
        self._envios: OrderedDict[int, tuple[float, int]] = OrderedDict()  # pn -> (t, destino)
        self._vistos: dict[tuple[Endereco, int], OrderedDict[int, None]] = {}
        self._rtt: dict[int, list[float]] = {}  # ID -> [srtt, rttvar]
        self._amostras_rtt: dict[int, deque] = {}  # ID -> últimas amostras de RTT
        self._sondas_pendentes: dict[int, tuple[float, int]] = {}  # pn -> (t, vizinho)

    # ------------------------------------------------------------ ciclo de vida
    async def start(self, host: str = "0.0.0.0", porta: int = PORTA) -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, local_addr=(host, porta))
        self._tarefa_vigia = asyncio.create_task(self._ciclo_vigia())
        log.info("[%s] à escuta em %s:%d", self.id, host, porta)

    def connection_made(self, transport) -> None:
        self._transport = transport

    def close(self) -> None:
        if self._tarefa_vigia:
            self._tarefa_vigia.cancel()
        for p in self._pendentes.values():
            if p.timer:
                p.timer.cancel()
        self._pendentes.clear()
        if self._transport:
            self._transport.close()

    @property
    def endereco_local(self) -> Endereco:
        return self._transport.get_extra_info("sockname")[:2]

    # ------------------------------------------------------------ vizinhos
    def set_id(self, meu_id: int) -> None:
        self.id = meu_id

    def set_address(self, no: int, addr: Endereco) -> None:
        """Conhecer o endereço de um nó que não é vizinho (ex: o bootstrapper, ID 0)."""
        self._enderecos[no] = addr

    def add_neighbor(self, no: int, addr: Endereco) -> None:
        self._enderecos[no] = addr
        if no not in self._vizinhos:
            self._vizinhos[no] = _Vizinho(addr, ultimo_rx=time.monotonic())

    def remove_neighbor(self, no: int) -> None:
        self._vizinhos.pop(no, None)
        self._cancelar_pendentes(no)
        self._esquecer_vistos(no)

    @property
    def neighbors(self) -> list[int]:
        return list(self._vizinhos)

    def set_intervalo(self, no: int, intervalo: float) -> None:
        """Intervalo de HELLO que o vizinho anunciou: o limite de silêncio passa a seguir este."""
        v = self._vizinhos.get(no)
        if v is not None:
            v.intervalo = intervalo

    def limite(self, no: int) -> float:
        """Tempo de silêncio ao fim do qual o vizinho é dado como em baixo (segundos)."""
        if self.idle_timeout is not None:
            return self.idle_timeout
        v = self._vizinhos.get(no)
        intervalo = v.intervalo if v else INTERVALO_HELLO
        return MULTIPLICADOR * max(intervalo, self._rto(no))

    def rtt(self, no: int) -> float | None:
        """RTT suavizado (segundos) até ao nó, ou None se ainda não há medições."""
        r = self._rtt.get(no)
        return r[0] if r else None

    def rtt_min(self, no: int) -> float | None:
        """Mínimo das últimas AMOSTRAS_RTT_MIN amostras de RTT (segundos), ou None."""
        amostras = self._amostras_rtt.get(no)
        return min(amostras) if amostras else None

    def jitter(self, no: int) -> float | None:
        """Variação do RTT (rttvar do RFC 6298, segundos), ou None."""
        r = self._rtt.get(no)
        return r[1] if r else None

    def perda(self, no: int) -> float | None:
        """Fração (0 a 1) das últimas AMOSTRAS_PERDA sondas sem ACK a tempo, ou None."""
        v = self._vizinhos.get(no)
        if v is None or not v.sondas:
            return None
        return v.sondas.count(False) / len(v.sondas)

    # ------------------------------------------------------------ envio
    def register(self) -> int:
        """Envia um REGISTER fiável para o bootstrapper, devolvendo o id da mensagem."""
        return self.send(BOOTSTRAPPER, Tipo.REGISTER, {}, fiavel=True)

    def send(
        self,
        destino: int,
        tipo: int,
        corpo: dict | bytes | None = None,
        fiavel: bool = False,
        fluxo: tuple[int, int] = FLUXO_CONTROLO,
        sonda: bool = False,
    ) -> int:
        """Envia uma mensagem. Devolve o id da mensagem (fiável) ou o nº do pacote.

        sonda=True: pede ACK mas não é retransmitida (o HELLO periódico). Se o destino é um
        vizinho, o ACK (ou a falta dele) entra nas medidas de RTT e de perda."""
        addr = self._enderecos.get(destino)
        if addr is None:
            raise ValueError(f"destino {destino} sem endereço conhecido")
        if sonda:
            pn = self._proximo_pn()
            if destino in self._vizinhos:
                self._sondas_pendentes[pn] = (time.monotonic(), destino)
            return self._enviar(addr, destino, tipo, codificar_corpo(corpo), fluxo,
                                Flag.FIAVEL, id_msg=pn, pn=pn)
        return self.send_to(destino, addr, tipo, corpo, fiavel, fluxo)

    def send_to(
        self,
        destino: int,
        addr: Endereco,
        tipo: int,
        corpo: dict | bytes | None = None,
        fiavel: bool = False,
        fluxo: tuple[int, int] = FLUXO_CONTROLO,
    ) -> int:
        """Envia para um endereço explícito, sem alterar a tabela de endereços."""
        dados = codificar_corpo(corpo)
        if not fiavel:
            return self._enviar(addr, destino, tipo, dados, fluxo)
        id_msg = self._proximo_pn()  # o 1.º pn da mensagem passa a ser a sua identidade
        p = _Pendente(destino, addr, tipo, dados, fluxo, id_msg)
        self._pendentes[id_msg] = p
        self._transmitir(p, pn=id_msg)
        return id_msg

    def _proximo_pn(self) -> int:
        self._pn = (self._pn + 1) & 0xFFFFFFFF
        return self._pn

    def _enviar(self, addr, destino, tipo, corpo, fluxo, flags=0, id_msg=None, pn=None) -> int:
        pn = pn if pn is not None else self._proximo_pn()
        pacote = Pacote(tipo, self.id, destino, pn, fluxo, flags, id_msg, corpo)
        if flags & Flag.FIAVEL:
            self._envios[pn] = (time.monotonic(), destino)
            if len(self._envios) > MAX_ENVIOS_GUARDADOS:
                self._envios.popitem(last=False)
        if self.perda_simulada and self._rand.random() < self.perda_simulada:
            log.debug("[%s] (perda simulada) %s pn=%d -> %s", self.id, nome_tipo(tipo), pn, destino)
            return pn
        self._transport.sendto(pacote.codificar(), addr)
        return pn

    def _transmitir(self, p: _Pendente, pn: int | None = None) -> None:
        pn = pn if pn is not None else self._proximo_pn()
        p.tentativas += 1
        p.pns.append(pn)
        self._pn_para_msg[pn] = p.id_msg
        if p.tentativas > 1:
            log.info("[%s] retransmissão %d de %s (msg %d) -> %s, pn=%d",
                     self.id, p.tentativas - 1, nome_tipo(p.tipo), p.id_msg, p.destino, pn)
        self._enviar(p.addr, p.destino, p.tipo, p.corpo, p.fluxo, Flag.FIAVEL, p.id_msg, pn)
        espera = min(self.rto_max, self._rto(p.destino) * 2 ** (p.tentativas - 1))  # backoff exponencial
        p.timer = asyncio.get_running_loop().call_later(espera, self._expirou, p.id_msg)

    def _rto(self, no: int) -> float:
        r = self._rtt.get(no)
        if not r:
            return self.rto_inicial
        srtt, rttvar = r
        return max(RTO_MIN, srtt + 4 * rttvar)  # RFC 6298

    def _expirou(self, id_msg: int) -> None:
        p = self._pendentes.get(id_msg)
        if p is None:
            return
        if p.destino in self._vizinhos:
            # Para um vizinho não se desiste: tenta-se até haver ACK ou até o limite de silêncio o dar
            # como em baixo (que cancela esta mensagem). Como no QUIC, só o silêncio decide
            # que um vizinho morreu; ACKs perdidos não chegam (vimos um falso alarme no CORE).
            if p.tentativas == self.max_tentativas:
                log.warning("[%s] %s (msg %d) para %s ainda sem ACK após %d tentativas",
                            self.id, nome_tipo(p.tipo), id_msg, p.destino, p.tentativas)
            self._transmitir(p)
            return
        if p.tentativas < self.max_tentativas:
            self._transmitir(p)
            return
        # Não vizinho (ex: bootstrapper): não há idle timeout, por isso desiste-se.
        log.warning("[%s] %s (msg %d) para %s sem ACK após %d tentativas, desisto",
                    self.id, nome_tipo(p.tipo), id_msg, p.destino, p.tentativas)
        self._esquecer(p)

    def _esquecer(self, p: _Pendente) -> None:
        if p.timer:
            p.timer.cancel()
        self._pendentes.pop(p.id_msg, None)
        for pn in p.pns:
            self._pn_para_msg.pop(pn, None)

    def _cancelar_pendentes(self, no: int) -> None:
        for p in [p for p in self._pendentes.values() if p.destino == no]:
            self._esquecer(p)

    # ------------------------------------------------------------ receção
    def datagram_received(self, dados: bytes, addr: Endereco) -> None:
        try:
            p = Pacote.descodificar(dados)
        except ValueError as e:
            log.debug("[%s] pacote inválido de %s: %s", self.id, addr, e)
            return
        # Um nó sem ID (ou o bootstrapper) aceita tudo; os outros só o que é para si.
        if self.id != SEM_ID and p.destino != self.id:
            log.debug("[%s] pacote para %s descartado", self.id, p.destino)
            return

        v = self._vizinhos.get(p.origem)
        if v:
            v.ultimo_rx = time.monotonic()

        if p.tipo == Tipo.ACK:
            self._processar_ack(p)
            return

        if p.fiavel:
            # Responde sempre, mesmo a duplicados: o ACK anterior pode ter-se perdido.
            self._enviar(addr, p.origem, Tipo.ACK, CORPO_ACK.pack(p.pn), FLUXO_CONTROLO)
            if self._duplicado(addr, p.origem, p.id_msg):
                log.debug("[%s] duplicado de %s (msg %d) ignorado", self.id, p.origem, p.id_msg)
                return

        try:
            corpo = descodificar_corpo(p.tipo, p.corpo)
        except ValueError as e:
            log.warning("[%s] corpo inválido em %s de %s: %s", self.id, nome_tipo(p.tipo), p.origem, e)
            return
        if self.on_message:
            self.on_message(p.origem, p.tipo, corpo, p.fluxo, addr)

    def _duplicado(self, addr: Endereco, origem: int, id_msg: int) -> bool:
        vistos = self._vistos.setdefault((addr, origem), OrderedDict())
        if id_msg in vistos:
            return True
        vistos[id_msg] = None
        if len(vistos) > MAX_VISTOS_POR_ORIGEM:
            vistos.popitem(last=False)
        return False

    def _esquecer_vistos(self, no: int) -> None:
        for chave in [c for c in self._vistos if c[1] == no]:
            del self._vistos[chave]

    def _processar_ack(self, p: Pacote) -> None:
        if len(p.corpo) < CORPO_ACK.size:
            return
        (pn,) = CORPO_ACK.unpack_from(p.corpo)
        envio = self._envios.pop(pn, None)
        if envio:
            t, destino = envio
            self._amostra_rtt(destino, time.monotonic() - t)
        sonda = self._sondas_pendentes.pop(pn, None)
        if sonda and sonda[1] in self._vizinhos:
            self._vizinhos[sonda[1]].sondas.append(True)  # a tempo: as atrasadas já saíram
        id_msg = self._pn_para_msg.get(pn)
        if id_msg is not None and id_msg in self._pendentes:
            self._esquecer(self._pendentes[id_msg])

    def _amostra_rtt(self, no: int, amostra: float) -> None:
        self._amostras_rtt.setdefault(no, deque(maxlen=AMOSTRAS_RTT_MIN)).append(amostra)
        r = self._rtt.get(no)
        if r is None:
            self._rtt[no] = [amostra, amostra / 2]
        else:  # média móvel exponencial, como no TCP (RFC 6298) e no QUIC (RFC 9002)
            r[1] = 0.75 * r[1] + 0.25 * abs(r[0] - amostra)
            r[0] = 0.875 * r[0] + 0.125 * amostra

    # ------------------------------------------------------------ vigia
    async def _ciclo_vigia(self) -> None:
        """Conta as sondas sem ACK como perdidas e dá como em baixo quem passou o limite."""
        while True:
            await asyncio.sleep(PASSO_VIGIA)
            agora = time.monotonic()
            for pn, (t, no) in list(self._sondas_pendentes.items()):
                if agora - t > ESPERA_ACK_SONDA:
                    del self._sondas_pendentes[pn]
                    if no in self._vizinhos:
                        self._vizinhos[no].sondas.append(False)
            for no, v in list(self._vizinhos.items()):
                if agora - v.ultimo_rx > self.limite(no):
                    self._vizinho_morreu(no, f"sem sinal há {agora - v.ultimo_rx:.1f} s")

    def _vizinho_morreu(self, no: int, motivo: str) -> None:
        if self._vizinhos.pop(no, None) is None:
            return
        self._cancelar_pendentes(no)
        self._esquecer_vistos(no)  # se voltar, é uma "ligação" nova
        log.warning("[%s] vizinho %s em baixo (%s)", self.id, no, motivo)
        if self.on_neighbor_down:
            self.on_neighbor_down(no)
