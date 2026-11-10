"""Pontas do vídeo no nó OTT: ligação por UDP, dentro da própria máquina, ao servidor e ao player.

    Servidor.py ──UDP local──▶ nó (servidor) ══ overlay ══▶ nó (cliente) ──UDP local──▶ Cliente.py

* Lado do servidor: o nó escuta em 127.0.0.1:PORTA (uma porta por vídeo) e recebe os pacotes RTP
  que o `player/Servidor.py` envia. A porta k-ésima da lista corresponde ao vídeo (stream) k.
* Lado do cliente: o nó envia os pacotes RTP, tal e qual chegaram, para o player em
  127.0.0.1:25000 (porta por omissão do `player/Cliente.py`).

O nó não mexe no conteúdo: o pacote RTP (cabeçalho de 12 B + frame JPEG) viaja inteiro, como
corpo de um `DATA`. Quem decide para onde vai cada frame (vizinhos, player) é o `NoOTT`.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Callable, Sequence

from .transporte import Endereco

log = logging.getLogger("ott.video")

LOCALHOST = "127.0.0.1"
PORTA_SERVIDOR = 24000  # 1.ª porta onde o nó espera o Servidor.py (24001 para o 2.º vídeo, ...)
PORTA_PLAYER = 25000  # porta onde o player (Cliente.py) escuta

# Chamado com (nº do vídeo, pacote RTP) por cada pacote que chega do servidor local.
OnFrameLocal = Callable[[int, bytes], None]


class _EntradaServidor(asyncio.DatagramProtocol):
    """Um socket UDP local por vídeo: tudo o que chega é um pacote RTP desse vídeo."""

    def __init__(self, stream: int, on_frame: OnFrameLocal):
        self.stream = stream
        self.on_frame = on_frame
        self.recebidos = 0

    def datagram_received(self, dados: bytes, addr: Endereco) -> None:
        if not dados:
            return
        self.recebidos += 1
        if self.recebidos == 1:
            log.info("vídeo %d: primeiro pacote do servidor local (%d B, de %s:%d)",
                     self.stream, len(dados), *addr)
        self.on_frame(self.stream, dados)

    def error_received(self, exc: Exception) -> None:
        log.warning("vídeo %d: erro no socket de entrada: %s", self.stream, exc)


class _SaidaPlayer(asyncio.DatagramProtocol):
    def error_received(self, exc: Exception) -> None:
        # Sem ligar o socket, o ICMP "porta inacessível" (player ainda fechado) nem chega aqui;
        # ficam só erros reais.
        log.warning("erro ao enviar para o player: %s", exc)


class PontaVideo:
    """As duas ligações locais do nó: entrada (servidor → nó) e saída (nó → player).

    portas_servidor: portas UDP locais onde escutar o servidor; a k-ésima é o vídeo k.
        Vazio = este nó não tem servidor local. A porta 0 escolhe uma livre (ver `portas`).
    player: onde entregar os pacotes RTP; None = este nó não entrega a nenhum player.
    """

    def __init__(
        self,
        on_frame_local: OnFrameLocal,
        portas_servidor: Sequence[int] = (),
        player: Endereco | None = None,
        *,
        host: str = LOCALHOST,
    ):
        self.on_frame_local = on_frame_local
        self.portas_pedidas = list(portas_servidor)
        self.player = player
        self.host = host
        self.portas: list[int] = []  # portas realmente usadas, pela ordem dos vídeos
        self.entregues_ao_player = 0
        self._entradas: list[asyncio.DatagramTransport] = []
        self._saida: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            for stream, porta in enumerate(self.portas_pedidas):
                transporte, _ = await loop.create_datagram_endpoint(
                    lambda s=stream: _EntradaServidor(s, self.on_frame_local),
                    local_addr=(self.host, porta),
                )
                self._entradas.append(transporte)
                self.portas.append(transporte.get_extra_info("sockname")[1])
                log.info("vídeo %d: à espera do servidor local em %s:%d",
                         stream, self.host, self.portas[-1])
            if self.player is not None:
                self._saida, _ = await loop.create_datagram_endpoint(
                    _SaidaPlayer, family=socket.AF_INET)  # sem remote_addr: ver _SaidaPlayer
                log.info("vídeo: a entregar ao player em %s:%d", *self.player)
        except BaseException:
            self.close()
            raise

    def entregar_ao_player(self, rtp: bytes) -> None:
        """Passa o pacote RTP ao player local, sem o alterar."""
        if self._saida is None or self.player is None:
            return
        if self.entregues_ao_player == 0:
            log.info("vídeo: primeiro pacote entregue ao player (%d B)", len(rtp))
        self.entregues_ao_player += 1
        self._saida.sendto(rtp, self.player)

    def close(self) -> None:
        for transporte in self._entradas:
            transporte.close()
        self._entradas.clear()
        if self._saida is not None:
            self._saida.close()
            self._saida = None
