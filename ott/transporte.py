"""Transporte "QUIC-like" sobre UDP: um socket por nó, para todos os vizinhos.

O que este módulo garante a quem o usa (bootstrapper, vizinhos, rotas, streaming):
  * send(..., fiavel=True): a mensagem chega uma e uma só vez, ou o vizinho é dado
    como morto. ACK e retransmissão são tratados aqui, não no código de cima.
    Para quem não é vizinho (ex: bootstrapper) desiste-se ao fim de MAX_TENTATIVAS.
  * send(..., fiavel=False): melhor esforço (vídeo, mensagens periódicas).
  * on_neighbor_down(id): chamado quando um vizinho deixa de dar sinal (idle timeout).
    Mensagens fiáveis para um vizinho são retransmitidas até haver ACK ou até ele cair.
  * rtt(id): RTT suavizado até ao vizinho, medido com os ACK (métrica da Etapa 2).

Ideias do QUIC usadas (ver docs/protocolo.md):
  * nº de pacote nunca reutilizado, nem nas retransmissões -> RTT sem ambiguidade;
  * a identidade da mensagem (id_msg) é separada do nº de pacote -> o recetor
    deteta duplicados quando um ACK se perde e a mensagem é retransmitida;
  * keepalive (PING) e idle timeout para detetar vizinhos mortos.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from .protocolo import (
    CORPO_ACK, FLUXO_CONTROLO, PORTA, SEM_ID, Flag, Pacote, Tipo,
    codificar_corpo, descodificar_corpo, nome_tipo,
)

log = logging.getLogger("ott.transporte")

Endereco = tuple[str, int]
OnMessage = Callable[[int, int, "dict | bytes", tuple[int, int], Endereco], None]
OnNeighborDown = Callable[[int], None]

# Valores por omissão dos temporizadores (segundos)
KEEPALIVE = 0.5  # envia KEEPALIVE se não enviou nada ao vizinho neste intervalo
# (com 1 s havia ~1,5 falsos "em baixo" por hora com 20% de perda; com 0,5 s, nenhum em 10 h:
#  ver scripts/sim_falsos_alarmes.py e o doc do protocolo)
IDLE_TIMEOUT = 3.5  # vizinho morto se não se recebe nada dele neste tempo (~7 keepalives)
RTO_INICIAL = 0.5  # timeout de retransmissão antes de haver medições de RTT
RTO_MIN = 0.2
RTO_MAX = 2.0  # teto do backoff, para não ficar minutos à espera
MAX_TENTATIVAS = 5  # para não vizinhos; para vizinhos só avisa e continua (decide o idle timeout)

MAX_ENVIOS_GUARDADOS = 4096  # para o cálculo do RTT
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
    ultimo_tx: float = 0.0


class Transporte(asyncio.DatagramProtocol):
    def __init__(
        self,
        meu_id: int = SEM_ID,
        on_message: OnMessage | None = None,
        on_neighbor_down: OnNeighborDown | None = None,
        *,
        keepalive: float = KEEPALIVE,
        idle_timeout: float = IDLE_TIMEOUT,
        rto_inicial: float = RTO_INICIAL,
        rto_max: float = RTO_MAX,
        max_tentativas: int = MAX_TENTATIVAS,
        perda: float = 0.0,
        seed: int | None = None,
    ):
        self.id = meu_id
        self.on_message = on_message
        self.on_neighbor_down = on_neighbor_down
        self.keepalive = keepalive
        self.idle_timeout = idle_timeout
        self.rto_inicial = rto_inicial
        self.rto_max = rto_max
        self.max_tentativas = max_tentativas
        self.perda = perda  # perda simulada no envio, só para testes fora do CORE
        self._rand = random.Random(seed)

        self._transport: asyncio.DatagramTransport | None = None
        self._tarefa_keepalive: asyncio.Task | None = None
        # Nº inicial aleatório, como o ISN do TCP: se o nó reiniciar, os ids das mensagens
        # novas não colidem com os que os vizinhos ainda guardam da execução anterior.
        self._pn = self._rand.randrange(1, 1 << 31)
        self._enderecos: dict[int, Endereco] = {}  # ID -> (ip, porta), inclui o bootstrapper
        self._vizinhos: dict[int, _Vizinho] = {}  # só estes levam keepalive / idle timeout
        self._pendentes: dict[int, _Pendente] = {}  # id_msg -> mensagem por confirmar
        self._pn_para_msg: dict[int, int] = {}  # pn -> id_msg (cada retransmissão tem pn novo)
        self._envios: OrderedDict[int, tuple[float, int]] = OrderedDict()  # pn -> (t, destino)
        self._vistos: dict[tuple[Endereco, int], OrderedDict[int, None]] = {}
        self._rtt: dict[int, list[float]] = {}  # ID -> [srtt, rttvar]

    # ------------------------------------------------------------ ciclo de vida
    async def start(self, host: str = "0.0.0.0", porta: int = PORTA) -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, local_addr=(host, porta))
        self._tarefa_keepalive = asyncio.create_task(self._ciclo_keepalive())
        log.info("[%s] à escuta em %s:%d", self.id, host, porta)

    def connection_made(self, transport) -> None:
        self._transport = transport

    def close(self) -> None:
        if self._tarefa_keepalive:
            self._tarefa_keepalive.cancel()
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

    def rtt(self, no: int) -> float | None:
        """RTT suavizado (segundos) até ao nó, ou None se ainda não há medições."""
        r = self._rtt.get(no)
        return r[0] if r else None

    # ------------------------------------------------------------ envio
    def send(
        self,
        destino: int,
        tipo: int,
        corpo: dict | bytes | None = None,
        fiavel: bool = False,
        fluxo: tuple[int, int] = FLUXO_CONTROLO,
    ) -> int:
        """Envia uma mensagem. Devolve o id da mensagem (fiável) ou o nº do pacote."""
        addr = self._enderecos.get(destino)
        if addr is None:
            raise ValueError(f"destino {destino} sem endereço conhecido")
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
        v = self._vizinhos.get(destino)
        if v:
            v.ultimo_tx = time.monotonic()
        if self.perda and self._rand.random() < self.perda:
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
            # Para um vizinho não se desiste: tenta-se até haver ACK ou até o idle timeout o dar
            # como em baixo (que cancela esta mensagem). Como no QUIC, só o idle timeout decide
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

        if p.tipo == Tipo.KEEPALIVE:
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
        id_msg = self._pn_para_msg.get(pn)
        if id_msg is not None and id_msg in self._pendentes:
            self._esquecer(self._pendentes[id_msg])

    def _amostra_rtt(self, no: int, amostra: float) -> None:
        r = self._rtt.get(no)
        if r is None:
            self._rtt[no] = [amostra, amostra / 2]
        else:  # média móvel exponencial, como no TCP (RFC 6298) e no QUIC (RFC 9002)
            r[1] = 0.75 * r[1] + 0.25 * abs(r[0] - amostra)
            r[0] = 0.875 * r[0] + 0.125 * amostra

    # ------------------------------------------------------------ keepalive
    async def _ciclo_keepalive(self) -> None:
        passo = min(self.keepalive, self.idle_timeout) / 4
        while True:
            await asyncio.sleep(passo)
            agora = time.monotonic()
            for no, v in list(self._vizinhos.items()):
                if agora - v.ultimo_rx > self.idle_timeout:
                    self._vizinho_morreu(no, f"sem sinal há {agora - v.ultimo_rx:.1f} s")
                elif agora - v.ultimo_tx >= self.keepalive:
                    # Pede ACK (para medir RTT) mas não é retransmitido: o próximo faz esse papel.
                    pn = self._proximo_pn()
                    self._enviar(v.addr, no, Tipo.KEEPALIVE, b"", FLUXO_CONTROLO,
                                 Flag.FIAVEL, id_msg=pn, pn=pn)

    def _vizinho_morreu(self, no: int, motivo: str) -> None:
        if self._vizinhos.pop(no, None) is None:
            return
        self._cancelar_pendentes(no)
        self._esquecer_vistos(no)  # se voltar, é uma "ligação" nova
        log.warning("[%s] vizinho %s em baixo (%s)", self.id, no, motivo)
        if self.on_neighbor_down:
            self.on_neighbor_down(no)
