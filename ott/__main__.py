"""Arranque de um nó OTT através do bootstrapper."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import random
import signal
import socket
import sys
from pathlib import Path
from dataclasses import dataclass, field
import time
from typing import Sequence

from .protocolo import PORTA, Tipo, nome_tipo
from . import rotas as modulo_rotas
from .rotas import Rotas, melhor_pela_margem
from .transporte import Endereco, Transporte
from .locais import PROJETO, ProgramasLocais, ficheiro_video
from .video import LOCALHOST, PORTA_PLAYER, PORTA_SERVIDOR, PontaVideo

log = logging.getLogger("ott")

RENOVACAO_JOIN = 2.0
VALIDADE_DESTINO = 6.0
PERIODO_PING = 5.0
SEM_VIDEO = 0.3  # s sem DATA do pai num fluxo ativo (6 frames a 20 por segundo): procurar outro pai
INSISTIR_REGISTO = 5.0  # s entre tentativas de registo, no arranque à mão (o bootstrapper pode faltar)
SEM_LEITOR = 2.0  # s sem frames do Servidor.py local (depois de já ter mandado): deixa de anunciar o vídeo
# HELLO periódico, que faz de apresentação, de sinal de vida e de nova tentativa de ligação
# (como o Hello do OSPF e os pacotes do BFD). Ver docs/protocolo.md.
HELLO_VIVO = 1.0  # intervalo para um vizinho vivo; é também o que cada nó anuncia no HELLO
HELLO_EM_BAIXO = 5.0  # intervalo para um vizinho que não responde (em baixo ou ainda a arrancar)
HELLOS_RAPIDOS = 3  # depois de cada mudança de estado, os 3 primeiros HELLOs vão a HELLO_VIVO
PASSO_HELLO = 0.05
MAX_ARRANQUE = (1 << 32) - 1
# Pedido de socorro ao "avô" (a única exceção à regra de só conhecer os vizinhos, autorizada pelo
# professor). Ver docs/protocolo.md, "Pedido de socorro ao avô".
ESPERA_RISCO = 3.0  # depois do registo, para os vizinhos terem tempo de se apresentar
FIM_EMERGENCIA = 6.0  # um vizinho original vivo há isto (3 rondas de anúncios): fecha a emergência


@dataclass
class EstadoVizinho:
    """O que um nó sabe de cada vizinho configurado.

    a arrancar: ainda não ouviu nenhum HELLO dele;
    a ouvir: ouve os HELLOs dele, mas ele ainda não o ouve a ele (como o Init do OSPF);
    vivo: ouvem-se os dois (os HELLOs dele trazem este nó em "ouvidos");
    em baixo: estava vivo e calou-se (ou deixou de o ouvir);
    saiu: avisou com CLOSE; não se lhe mandam HELLOs, porque é ele que se apresenta ao voltar.
    """
    estado: str = "a arrancar"
    rapidos: int = HELLOS_RAPIDOS  # HELLOs que ainda vão a HELLO_VIVO, sem resposta
    proximo: float = 0.0  # quando enviar o próximo HELLO
    arranque: int | None = None  # nº de arranque visto nos HELLOs dele
    ja_vivo: bool = False  # já esteve vivo (para dizer "voltou" em vez de "ativo")
    vivo_desde: float = 0.0  # quando passou a vivo pela última vez


@dataclass
class FluxoVideo:
    pai: int | None = None
    destinos: dict[int, float | None] = field(default_factory=dict)
    local: bool = False

    @property
    def ativo(self) -> bool:
        return self.local or bool(self.destinos)


class NoOTT:
    def __init__(
        self,
        ip_bootstrapper: str,
        *,
        porta_bootstrapper: int = PORTA,
        host: str = "0.0.0.0",
        porta: int = PORTA,
        porta_vizinhos: int | None = None,
        portas_servidor: Sequence[int] = (),
        player: Endereco | None = None,
        host_video: str = LOCALHOST,
    ):
        self.endereco_bootstrapper = (ip_bootstrapper, porta_bootstrapper)
        self.host = host
        self.porta = porta
        self.porta_vizinhos = porta if porta_vizinhos is None else porta_vizinhos
        self.transporte = Transporte(on_message=self._receber, on_neighbor_down=self._vizinho_em_baixo)
        self._registo: asyncio.Future[None] | None = None
        self._vizinhos_configurados: dict[int, Endereco] = {}
        self._vizinhos_estado: dict[int, EstadoVizinho] = {}
        # Sorteado em cada arranque: quem o vir mudar nos HELLOs sabe que este nó reiniciou.
        self.arranque = random.SystemRandom().randint(1, MAX_ARRANQUE)
        self._registado = False
        # Vídeo: ligações locais (UDP em localhost) ao servidor e ao player.
        # `fluxos_player` é provisório: na Etapa 2 passa a ser preenchido pelas rotas
        # (JOIN / anúncios) em vez de configurado à mão.
        self.video = PontaVideo(self._frame_do_servidor, portas_servidor, player, host=host_video)
        self.fluxos_player: set[tuple[int, int]] = set()  # fluxos entregues ao player local
        # --ver k: vídeos pedidos ao servidor mais barato, e o servidor escolhido para cada um
        self.videos_pedidos: set[int] = set()
        self._servidor_do_video: dict[int, int] = {}
        self._primeira_rota_do_video: dict[int, float] = {}  # quando apareceu a 1.ª opção
        self.tabela_fluxos: dict[tuple[int, int], FluxoVideo] = {}  # pai e destinos de cada fluxo
        self.rotas = Rotas(self.transporte, self._pai_mudou)  # escolhe o pai de cada servidor
        self._tarefa_hello: asyncio.Task | None = None
        self._tarefas_aplicacao: list[asyncio.Task] = []
        self._ping_seq: dict[tuple[int, int], int] = {}
        self._ultima_renovacao: dict[tuple[int, int], float] = {}
        # fluxo -> hora do último DATA do pai; só existe depois do 1.º DATA desse pai
        self._ultimo_video: dict[tuple[int, int], float] = {}
        self._em_encerramento = False
        self._ultimo_local: dict[int, float] = {}  # vídeo -> último frame do Servidor.py local
        self._videos_parados: set[int] = set()  # vídeos locais que deixaram de chegar
        # Pedido de socorro ao avô
        self._registado_em = 0.0
        self._em_risco: int | None = None  # o único vizinho vivo, a quem se pediu o avô
        self._avos: dict[int, Endereco] = {}  # avô(s), guardados só enquanto o nó está em risco
        self._socorro_pedido = False  # já se apresentou aos avós desde que ficou sem vizinhos
        self._protegidos: set[int] = set()  # vizinhos que nos pediram o avô (avisar se mudar)
        self._emergencia: set[int] = set()  # vizinhos que não vêm do bootstrapper (ligação ao avô)
        self._avos_ligados: set[int] = set()  # os desses a quem este nó pediu socorro (só este lado
        # fecha a ligação quando um vizinho original volta; o avô limita-se a aceitar)

    @property
    def _confirmados(self) -> set[int]:
        """Vizinhos vivos: os dois já se ouvem."""
        return {no for no, ev in self._vizinhos_estado.items() if ev.estado == "vivo"}

    async def start(self, timeout: float = 15, insistir: bool = False) -> None:
        """Regista-se no bootstrapper. Sem resposta em `timeout` s: erro, ou, com `insistir`
        (o arranque à mão), volta a tentar de INSISTIR_REGISTO em INSISTIR_REGISTO s para sempre,
        para o nó poder arrancar antes do bootstrapper ou sobreviver a ele ter caído."""
        self._registo = asyncio.get_running_loop().create_future()
        await self.transporte.start(self.host, self.porta)
        await self.video.start()
        self.transporte.set_address(0, self.endereco_bootstrapper)
        endereco = f"{self.endereco_bootstrapper[0]}:{self.endereco_bootstrapper[1]}"
        while True:
            self.transporte.register()
            espera = INSISTIR_REGISTO if insistir else timeout
            try:
                await asyncio.wait_for(asyncio.shield(self._registo), espera)
                break
            except asyncio.TimeoutError as exc:
                if not insistir:
                    self.close()
                    raise RuntimeError(
                        f"sem resposta do bootstrapper {endereco} após {timeout:g} segundos"
                    ) from exc
                log.warning("sem resposta do bootstrapper %s: volto a tentar daqui a %g s",
                            endereco, INSISTIR_REGISTO)
        self._iniciar_tarefas_aplicacao()

    async def run(self) -> None:
        try:
            await self.start()
            await asyncio.Event().wait()
        finally:
            await self.shutdown()

    def close(self) -> None:
        self._em_encerramento = True
        self._cancelar_tarefas()
        self.video.close()
        self.transporte.close()

    async def shutdown(self, timeout: float = 1.0) -> None:
        """Avisa os vizinhos antes de fechar os sockets, aguardando os ACKs até ao limite."""
        if self._em_encerramento:
            return
        self._em_encerramento = True
        self._cancelar_tarefas()
        mensagens = []
        if self._registado:
            for vizinho in sorted(self._confirmados):
                mensagens.append(
                    (vizinho, self.transporte.send(vizinho, Tipo.CLOSE, {}, fiavel=True))
                )
        limite = asyncio.get_running_loop().time() + max(0.0, timeout)
        while mensagens and asyncio.get_running_loop().time() < limite:
            if not any(self.transporte.pendente(no, msg) for no, msg in mensagens):
                break
            await asyncio.sleep(0.01)
        self.video.close()
        self.transporte.close()

    def _cancelar_tarefas(self) -> None:
        if self._tarefa_hello is not None:
            self._tarefa_hello.cancel()
            self._tarefa_hello = None
        for tarefa in self._tarefas_aplicacao:
            tarefa.cancel()
        self._tarefas_aplicacao.clear()

    def _receber(self, origem: int, tipo: int, corpo: dict | bytes,
                 fluxo: tuple[int, int], addr: Endereco) -> None:
        if not self._registado:
            if tipo == Tipo.REGISTER_ACK:
                self._processar_registo(origem, corpo, addr)
            return

        if tipo == Tipo.HELLO:
            self._processar_hello(origem, corpo, addr)
            return

        if tipo == Tipo.CLOSE:
            if corpo != {}:
                log.warning("[%s] corpo inválido em CLOSE de %s", self.transporte.id, origem)
                return
            if origem not in self._confirmados:
                return
            self._perder_vizinho(origem, "saiu")
            log.info("[%s] vizinho %s avisou que vai sair", self.transporte.id, origem)
            if origem in self._emergencia:
                self._esquecer_emergencia(origem)
            return

        if tipo == Tipo.ANNOUNCE:
            if origem in self._confirmados and isinstance(corpo, dict):
                self.rotas.receber(origem, corpo)
            return

        if tipo == Tipo.WITHDRAW:
            if origem in self._confirmados and isinstance(corpo, dict):
                self.rotas.receber_withdraw(origem, corpo)
            return

        if tipo == Tipo.JOIN:
            self._receber_join(origem, fluxo)
            return

        if tipo == Tipo.LEAVE:
            self._receber_leave(origem, fluxo)
            return

        if tipo == Tipo.PING and isinstance(corpo, dict):
            self._receber_ping(origem, fluxo, corpo)
            return

        if tipo == Tipo.SOCORRO:
            self._receber_socorro(origem)
            return

        if tipo == Tipo.AVO and isinstance(corpo, dict):
            self._receber_avo(origem, corpo)
            return

        if tipo == Tipo.DATA and isinstance(corpo, bytes):
            self._receber_video(origem, fluxo, corpo)
            return

        log.warning("[%s] mensagem %s inesperada de %s", self.transporte.id,
                    nome_tipo(tipo), addr)

    # ------------------------------------------------------------ vídeo
    def _frame_do_servidor(self, stream: int, rtp: bytes) -> None:
        """Pacote RTP vindo do Servidor.py local: passa a ser o fluxo (meu ID, stream)."""
        self._ultimo_local[stream] = time.monotonic()
        if not self._registado:
            return  # ainda sem ID, não há fluxo para nomear
        fluxo = (self.transporte.id, stream)
        if self._ativo(fluxo):
            self._distribuir_video(fluxo, rtp)

    def _distribuir_video(self, fluxo: tuple[int, int], rtp: bytes) -> None:
        """Entrega o pacote ao player local e/ou reenvia-o aos vizinhos a jusante."""
        if fluxo in self.fluxos_player:
            self.video.entregar_ao_player(rtp)
        for vizinho in self._destinos(fluxo):
            if vizinho in self.transporte.neighbors:
                self.transporte.send(vizinho, Tipo.DATA, rtp, fluxo=fluxo)  # sem fiabilidade

    def pedir_video(self, fluxo: tuple[int, int]) -> None:
        """Regista o interesse do player local; o JOIN sobe quando houver pai."""
        self._validar_fluxo(fluxo)
        self.fluxos_player.add(fluxo)
        estado = self._estado_fluxo(fluxo)
        estado.local = True
        if self._registado and estado.pai is not None:
            self._enviar_join(fluxo, estado.pai, fiavel=True)

    def desistir_video(self, fluxo: tuple[int, int]) -> None:
        """Remove o interesse local e propaga LEAVE se já não houver destinos."""
        self.fluxos_player.discard(fluxo)
        estado = self.tabela_fluxos.get(fluxo)
        if estado is not None:
            estado.local = False
        self._propagar_estado_fluxo(fluxo)

    def definir_pai(self, fluxo: tuple[int, int], pai: int | None) -> None:
        """Ponto de integração para a descoberta de rotas, ainda fora deste âmbito."""
        self._validar_fluxo(fluxo)
        if pai is not None and pai not in self._vizinhos_configurados:
            raise ValueError(f"o pai {pai} não é um vizinho configurado")
        estado = self._estado_fluxo(fluxo)
        anterior = estado.pai
        if anterior == pai:
            return
        if anterior is not None and self._ativo(fluxo) and anterior in self.transporte.neighbors:
            self.transporte.send(anterior, Tipo.LEAVE, {}, fiavel=True, fluxo=fluxo)
        estado.pai = pai
        self._ultimo_video.pop(fluxo, None)  # o pai novo ainda não enviou vídeo: não vigiar já
        if pai is not None and self._registado and self._ativo(fluxo) and not self._servidor_local(fluxo):
            self._enviar_join(fluxo, pai, fiavel=True)
        self._registar_tabela()

    @staticmethod
    def _validar_fluxo(fluxo: tuple[int, int]) -> None:
        servidor, stream = fluxo
        if (isinstance(servidor, bool) or not isinstance(servidor, int)
                or not 1 <= servidor <= 65535
                or isinstance(stream, bool) or not isinstance(stream, int)
                or not 0 <= stream <= 255):
            raise ValueError(f"fluxo inválido: {fluxo!r}")

    def _pai_mudou(self, servidor: int, pai: int | None) -> None:
        """As rotas escolheram outro pai para o servidor: aplica-o aos vídeos dele."""
        for fluxo in sorted({*self.tabela_fluxos, *self.fluxos_player}):
            if fluxo[0] == servidor:
                self.definir_pai(fluxo, pai)
        self._escolher_servidores()
        for protegido in sorted(self._protegidos & self._confirmados):
            self._enviar_avos(protegido)  # o avô de quem nos pediu socorro mudou

    # ------------------------------------------------------------ pedido de socorro ao avô
    def _rever_socorro(self, agora: float) -> None:
        """Corre de 0,25 em 0,25 s. Um nó com um só vizinho vivo está em risco: pede-lhe o avô.
        Se ficar sem nenhum, apresenta-se ao avô. Quando um vizinho original volta (e já teve tempo
        de anunciar rotas), fecha a ligação de emergência e a exceção acaba."""
        if not self._registado or agora - self._registado_em < ESPERA_RISCO:
            return
        vivos = self._confirmados
        if len(vivos) == 1:
            (unico,) = vivos
            self._socorro_pedido = False
            if self._em_risco != unico:
                self._em_risco = unico
                self._avos.clear()
                log.info("[%s] só um vizinho vivo (%s): pede-lhe o avô", self.transporte.id, unico)
                self.transporte.send(unico, Tipo.SOCORRO, {}, fiavel=True)
        elif vivos:
            self._socorro_pedido = False
            if self._em_risco is not None:
                log.info("[%s] já tem %d vizinhos vivos: deixa de guardar o avô",
                         self.transporte.id, len(vivos))
                self._em_risco = None
                self._avos.clear()
        else:
            self._em_risco = None
            if self._avos and not self._socorro_pedido:
                self._socorro_pedido = True
                self._pedir_socorro()
        if self._avos_ligados:
            originais = [no for no in vivos if no not in self._emergencia
                         and agora - self._vizinhos_estado[no].vivo_desde >= FIM_EMERGENCIA]
            if originais:
                for no in sorted(self._avos_ligados):
                    log.info("[%s] o vizinho %s voltou: fecha a ligação de emergência ao %s",
                             self.transporte.id, originais[0], no)
                    if no in self._confirmados:
                        self.transporte.send(no, Tipo.CLOSE, {}, fiavel=True)
                    self._esquecer_emergencia(no)

    def _pedir_socorro(self) -> None:
        """Sem vizinhos vivos: apresenta-se ao(s) avô(s) com HELLOs que levam "socorro"."""
        agora = time.monotonic()
        for avo, endereco in sorted(self._avos.items()):
            if avo == self.transporte.id or avo in self._vizinhos_configurados:
                continue  # é ele próprio, ou já é vizinho (então também está em baixo)
            self._vizinhos_configurados[avo] = endereco
            self._vizinhos_estado[avo] = EstadoVizinho(proximo=agora)
            self._emergencia.add(avo)
            self._avos_ligados.add(avo)
            self.transporte.set_address(avo, endereco)
            log.warning("[%s] sem vizinhos vivos: pede socorro ao avô %s (%s)",
                        self.transporte.id, avo, endereco[0])

    def _esquecer_emergencia(self, no: int) -> None:
        """Fim de uma ligação de emergência: o nó deixa de ser vizinho (não se tenta de novo)."""
        self._emergencia.discard(no)
        self._avos_ligados.discard(no)
        if no in self.transporte.neighbors:
            self.transporte.remove_neighbor(no)
        self._limpar_estado_vizinho(no)
        self._vizinhos_estado.pop(no, None)
        self._vizinhos_configurados.pop(no, None)
        log.info("[%s] ligação de emergência ao %s terminada", self.transporte.id, no)

    def _receber_socorro(self, origem: int) -> None:
        if origem not in self._confirmados:
            return
        self._protegidos.add(origem)
        self._enviar_avos(origem)

    def _enviar_avos(self, destino: int) -> None:
        """Diz a quem pediu socorro quem são os pais deste nó (os avôs dele), com o endereço."""
        avos = []
        for srv in sorted(self.rotas.servidores):
            pai = self.rotas.pai(srv)
            if pai is None or pai == destino or pai in (a["id"] for a in avos):
                continue
            endereco = self._vizinhos_configurados.get(pai)
            if endereco is not None:
                avos.append({"id": pai, "ip": endereco[0], "porta": endereco[1]})
        log.info("[%s] o vizinho %s pediu o avô: %s", self.transporte.id, destino,
                 ", ".join(str(a["id"]) for a in avos) or "ainda não tenho pai")
        self.transporte.send(destino, Tipo.AVO, {"avos": avos}, fiavel=True)

    def _receber_avo(self, origem: int, corpo: dict) -> None:
        if origem not in self._confirmados or self._em_risco != origem:
            return  # só se guarda o avô enquanto se está em risco, e vindo de quem se pediu
        lista = corpo.get("avos")
        if not isinstance(lista, list):
            return
        avos = {}
        for a in lista:
            try:
                no, ip, porta = a["id"], a["ip"], a["porta"]
                if (isinstance(no, bool) or not isinstance(no, int) or not 1 <= no <= 65535
                        or isinstance(porta, bool) or not isinstance(porta, int)
                        or not 1 <= porta <= 65535):
                    raise ValueError
                ipaddress.ip_address(ip)
            except (KeyError, TypeError, ValueError):
                log.warning("[%s] AVO inválido de %s: %r", self.transporte.id, origem, a)
                return
            avos[no] = (ip, porta)
        self._avos = avos
        log.info("[%s] avô guardado (via %s): %s", self.transporte.id, origem,
                 ", ".join(f"{no} ({ip})" for no, (ip, _p) in sorted(avos.items())) or "nenhum")

    def _escolher_servidores(self) -> None:
        """--ver k: pede o vídeo k ao servidor mais barato que o anuncia. Troca de servidor se o
        atual ficar sem rota, ou se outro for mais barato pela margem (LEAVE do fluxo antigo,
        JOIN do novo). O vídeo k é o mesmo em todos os servidores."""
        for video in sorted(self.videos_pedidos):
            opcoes = {}
            for srv in self.rotas.servidores:
                rota = self.rotas.rota(srv)
                if rota is not None and video in rota.videos:
                    opcoes[srv] = rota.custo
            if not opcoes:
                continue  # sem rota para nenhum: fica com o atual até alguém voltar
            melhor = min(opcoes, key=opcoes.get)
            atual = self._servidor_do_video.get(video)
            if atual is None:
                # 1.ª escolha: espera duas rondas para ouvir todos os servidores. Senão ficava
                # com o primeiro a chegar, e o mais perto quase nunca ganha pela margem.
                inicio = self._primeira_rota_do_video.setdefault(video, time.monotonic())
                if time.monotonic() - inicio < 2 * modulo_rotas.PERIODO_ANUNCIO:
                    continue
            if melhor == atual or (atual in opcoes
                                   and not melhor_pela_margem(opcoes[melhor], opcoes[atual])):
                continue
            if atual is not None:
                self.desistir_video((atual, video))
            self._servidor_do_video[video] = melhor
            log.info("[%s] vídeo %s: servidor %s (custo %.1f)%s", self.transporte.id, video,
                     melhor, opcoes[melhor], "" if atual is None else f", antes {atual}")
            self.pedir_video((melhor, video))

    def _videos_a_anunciar(self) -> list[int]:
        """Os vídeos do servidor local que se anunciam: todos, menos os que já mandaram frames e
        se calaram há mais de SEM_LEITOR s (o Servidor.py morreu). Os clientes desse vídeo passam
        para o outro servidor; quando os frames voltam, o vídeo volta a ser anunciado."""
        agora = time.monotonic()
        videos = []
        for k in range(len(self.video.portas_pedidas)):
            ultimo = self._ultimo_local.get(k)
            parado = ultimo is not None and agora - ultimo > SEM_LEITOR
            if parado and k not in self._videos_parados:
                log.warning("[%s] vídeo %d: o servidor local deixou de mandar frames; deixo de o "
                            "anunciar", self.transporte.id, k)
            elif not parado and k in self._videos_parados:
                log.info("[%s] vídeo %d: o servidor local voltou; volto a anunciá-lo",
                         self.transporte.id, k)
            (self._videos_parados.add if parado else self._videos_parados.discard)(k)
            if not parado:
                videos.append(k)
        return videos

    def parar_de_ver(self) -> None:
        """O player local fechou: deixa de pedir os vídeos (LEAVE se ninguém mais os quiser)."""
        fluxos = sorted(self.fluxos_player)
        self.videos_pedidos.clear()
        self._servidor_do_video.clear()
        for fluxo in fluxos:
            self.desistir_video(fluxo)
        log.info("[%s] o player fechou: deixo de pedir o vídeo %s", self.transporte.id,
                 ", ".join(str(f) for f in fluxos) or "(nenhum)")

    def _estado_fluxo(self, fluxo: tuple[int, int]) -> FluxoVideo:
        estado = self.tabela_fluxos.get(fluxo)
        if estado is None:  # fluxo novo: herda o pai já escolhido para o servidor
            estado = self.tabela_fluxos[fluxo] = FluxoVideo(pai=self.rotas.pai(fluxo[0]))
        estado.local = fluxo in self.fluxos_player
        return estado

    def _ativo(self, fluxo: tuple[int, int]) -> bool:
        estado = self.tabela_fluxos.get(fluxo)
        return bool(estado and estado.ativo)

    def _pai(self, fluxo: tuple[int, int]) -> int | None:
        estado = self.tabela_fluxos.get(fluxo)
        return estado.pai if estado else None

    def _destinos(self, fluxo: tuple[int, int]) -> list[int]:
        estado = self.tabela_fluxos.get(fluxo)
        return list(estado.destinos) if estado else []

    def _servidor_local(self, fluxo: tuple[int, int]) -> bool:
        return (
            self._registado
            and fluxo[0] == self.transporte.id
            and 0 <= fluxo[1] < len(self.video.portas_pedidas)
        )

    def _receber_join(self, origem: int, fluxo: tuple[int, int]) -> None:
        if origem not in self._confirmados or origem == self._pai(fluxo):
            log.warning("[%s] JOIN inválido de %s para %s", self.transporte.id, origem, fluxo)
            return
        try:
            self._validar_fluxo(fluxo)
        except ValueError:
            log.warning("[%s] JOIN com fluxo inválido de %s: %s", self.transporte.id,
                        origem, fluxo)
            return
        estado = self._estado_fluxo(fluxo)
        ja_ativo = self._ativo(fluxo)
        estado.destinos[origem] = time.monotonic() + VALIDADE_DESTINO
        if not ja_ativo:
            self._propagar_estado_fluxo(fluxo)
        self._registar_tabela()

    def _receber_leave(self, origem: int, fluxo: tuple[int, int]) -> None:
        estado = self.tabela_fluxos.get(fluxo)
        if origem not in self._confirmados or estado is None:
            return
        estado.destinos.pop(origem, None)
        self._propagar_estado_fluxo(fluxo)
        self._registar_tabela()

    def _propagar_estado_fluxo(self, fluxo: tuple[int, int]) -> None:
        pai = self._pai(fluxo)
        if pai is None or not self._registado or self._servidor_local(fluxo):
            return
        if self._ativo(fluxo):
            self._enviar_join(fluxo, pai, fiavel=True)
        elif pai in self.transporte.neighbors:
            self.transporte.send(pai, Tipo.LEAVE, {}, fiavel=True, fluxo=fluxo)
            self._ultima_renovacao.pop(fluxo, None)

    def _enviar_join(self, fluxo: tuple[int, int], pai: int, *, fiavel: bool) -> None:
        self.transporte.send(pai, Tipo.JOIN, {}, fiavel=fiavel, fluxo=fluxo)
        self._ultima_renovacao[fluxo] = time.monotonic()

    def _receber_video(self, origem: int, fluxo: tuple[int, int], rtp: bytes) -> None:
        if origem != self._pai(fluxo) or not self._ativo(fluxo):
            log.debug("[%s] DATA de %s para fluxo %s descartado", self.transporte.id,
                      origem, fluxo)
            return
        self._ultimo_video[fluxo] = time.monotonic()
        self._distribuir_video(fluxo, rtp)

    def _vigiar_video(self, agora: float) -> None:
        """Fluxo ativo sem DATA do pai há mais de SEM_VIDEO: troca de pai, se houver viável."""
        for fluxo, ultimo in list(self._ultimo_video.items()):
            if not self._ativo(fluxo) or self._pai(fluxo) is None:
                self._ultimo_video.pop(fluxo, None)  # volta a vigiar depois do próximo DATA
            elif agora - ultimo > SEM_VIDEO:
                # Sem vizinho viável fica com o pai e tenta outra vez na volta seguinte.
                self.rotas.pai_sem_video(fluxo[0], agora - ultimo)

    def _receber_ping(self, origem: int, fluxo: tuple[int, int], corpo: dict) -> None:
        if origem != self._pai(fluxo) or not self._ativo(fluxo):
            return
        numero = corpo.get("n")
        caminho = corpo.get("caminho")
        if (isinstance(numero, bool) or not isinstance(numero, int)
                or not isinstance(caminho, list)
                or not caminho
                or any(not isinstance(passo, dict)
                       or isinstance(passo.get("id"), bool)
                       or not isinstance(passo.get("id"), int)
                       or isinstance(passo.get("t"), bool)
                       or not isinstance(passo.get("t"), (int, float)) for passo in caminho)
                or any(passo["id"] == self.transporte.id for passo in caminho)):
            log.warning("[%s] PING inválido de %s para %s", self.transporte.id, origem, fluxo)
            return
        caminho = [*caminho, {"id": self.transporte.id, "t": time.time()}]
        self._registar_ping(numero, fluxo, caminho)
        corpo_seguinte = {"n": numero, "caminho": caminho}
        for destino in self._destinos(fluxo):
            if destino in self.transporte.neighbors:
                self.transporte.send(destino, Tipo.PING, corpo_seguinte, fluxo=fluxo)

    def _registar_ping(self, numero: int, fluxo: tuple[int, int], caminho: list[dict]) -> None:
        partes = [str(caminho[0]["id"])]
        for anterior, atual in zip(caminho, caminho[1:]):
            ms = (atual["t"] - anterior["t"]) * 1000
            partes.append(f"{atual['id']} (+{ms:.1f} ms)")
        log.info("PING %d do vídeo %s: %s", numero, fluxo, " → ".join(partes))

    def _iniciar_tarefas_aplicacao(self) -> None:
        if self._tarefas_aplicacao:
            return
        self._tarefas_aplicacao = [
            asyncio.create_task(self._ciclo_fluxos()),
            asyncio.create_task(self._ciclo_ping()),
        ]
        if self.video.portas_pedidas:
            self._tarefas_aplicacao.append(
                asyncio.create_task(self.rotas.anunciar_servidor(self._videos_a_anunciar)))

    async def _ciclo_fluxos(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            self.rotas.expirar()
            self._escolher_servidores()  # os custos mudam a cada ronda, mesmo sem mudar o pai
            agora = time.monotonic()
            self._vigiar_video(agora)
            self._rever_socorro(agora)
            for fluxo, estado in list(self.tabela_fluxos.items()):
                expirados = [
                    vizinho for vizinho, limite in estado.destinos.items()
                    if limite is not None and agora >= limite
                ]
                if not expirados:
                    continue
                ativo_antes = self._ativo(fluxo)
                for vizinho in expirados:
                    estado.destinos.pop(vizinho, None)
                if ativo_antes and not self._ativo(fluxo):
                    self._propagar_estado_fluxo(fluxo)
            for fluxo, estado in list(self.tabela_fluxos.items()):
                if (self._ativo(fluxo) and estado.pai is not None
                        and not self._servidor_local(fluxo)
                        and agora - self._ultima_renovacao.get(fluxo, 0) >= RENOVACAO_JOIN):
                    self._enviar_join(fluxo, estado.pai, fiavel=False)

    async def _ciclo_ping(self) -> None:
        while True:
            await asyncio.sleep(PERIODO_PING)
            for fluxo in list(self.tabela_fluxos):
                if not self._servidor_local(fluxo) or not self._ativo(fluxo):
                    continue
                numero = self._ping_seq.get(fluxo, 0) + 1
                self._ping_seq[fluxo] = numero
                caminho = [{"id": self.transporte.id, "t": time.time()}]
                self._registar_ping(numero, fluxo, caminho)
                corpo = {"n": numero, "caminho": caminho}
                for destino in self._destinos(fluxo):
                    if destino in self.transporte.neighbors:
                        self.transporte.send(destino, Tipo.PING, corpo, fluxo=fluxo)

    def _processar_registo(self, origem: int, corpo: dict | bytes, addr: Endereco) -> None:
        if self._registo is None or self._registo.done():
            return
        if origem != 0 or addr != self.endereco_bootstrapper:
            log.warning("REGISTER_ACK inesperado de %s (origem=%d)", addr, origem)
            return
        try:
            if not isinstance(corpo, dict):
                raise ValueError("o corpo não é um objeto")
            identificador = corpo.get("id")
            if isinstance(identificador, bool) or not isinstance(identificador, int) or not 1 <= identificador <= 65535:
                raise ValueError("'id' inválido")
            lista_vizinhos = corpo.get("vizinhos")
            if not isinstance(lista_vizinhos, list):
                raise ValueError("'vizinhos' não é uma lista")

            vizinhos: dict[int, Endereco] = {}
            for vizinho in lista_vizinhos:
                if not isinstance(vizinho, dict):
                    raise ValueError("entrada de vizinho inválida")
                no = vizinho.get("id")
                ip = vizinho.get("ip")
                if isinstance(no, bool) or not isinstance(no, int) or not 1 <= no <= 65535:
                    raise ValueError("ID de vizinho inválido")
                if no == identificador or no in vizinhos:
                    raise ValueError(f"ID de vizinho duplicado ou igual ao próprio nó: {no}")
                if not isinstance(ip, str):
                    raise ValueError(f"IP inválido para o vizinho {no}")
                ipaddress.ip_address(ip)
                vizinhos[no] = (ip, self.porta_vizinhos)

            self.transporte.set_id(identificador)
            self._vizinhos_configurados = vizinhos
            self._registado = True
            self._registado_em = time.monotonic()
            for no, endereco in vizinhos.items():
                self.transporte.set_address(no, endereco)
                self._vizinhos_estado[no] = EstadoVizinho()
            self._tarefa_hello = asyncio.create_task(self._ciclo_hello())
            for fluxo in self.fluxos_player:
                self._estado_fluxo(fluxo)
            for stream in range(len(self.video.portas_pedidas)):
                self._estado_fluxo((identificador, stream))
            log.info("[%s] registo aceite; vizinhos configurados: %d",
                     identificador, len(vizinhos))
            self._registar_tabela()
            self._registo.set_result(None)
        except (ValueError, TypeError) as exc:
            self._registo.set_exception(ValueError(f"REGISTER_ACK inválido: {exc}"))

    # ------------------------------------------------------------ vizinhos (HELLO)
    def _ouvidos(self) -> list[int]:
        """Vizinhos de quem este nó está a ouvir HELLOs (vão no HELLO, como no OSPF)."""
        return sorted(no for no, ev in self._vizinhos_estado.items()
                      if ev.estado in ("a ouvir", "vivo"))

    async def _ciclo_hello(self) -> None:
        while True:
            agora = time.monotonic()
            for no, ev in list(self._vizinhos_estado.items()):
                if ev.estado != "saiu" and agora >= ev.proximo:
                    self._enviar_hello(no, ev, agora)
            await asyncio.sleep(PASSO_HELLO)

    def _enviar_hello(self, no: int, ev: EstadoVizinho, agora: float) -> None:
        corpo = {"arranque": self.arranque, "intervalo": HELLO_VIVO, "ouvidos": self._ouvidos()}
        if no in self._avos_ligados:
            corpo["socorro"] = True  # para o avô aceitar um vizinho que não vem do bootstrapper
        try:
            self.transporte.send(no, Tipo.HELLO, corpo, sonda=True)
        except ValueError:
            return
        if ev.estado == "vivo":
            ev.proximo = agora + HELLO_VIVO
            return
        if ev.rapidos > 0:
            ev.rapidos -= 1
            ev.proximo = agora + HELLO_VIVO
            if ev.rapidos == 0:  # o próximo já vai devagar
                log.info("[%s] vizinho %s sem resposta a %d HELLOs: passa a tentar de %g em %g s",
                         self.transporte.id, no, HELLOS_RAPIDOS, HELLO_EM_BAIXO, HELLO_EM_BAIXO)
        else:
            ev.proximo = agora + HELLO_EM_BAIXO

    @staticmethod
    def _validar_hello(corpo) -> tuple[int, float, list[int]]:
        if not isinstance(corpo, dict):
            raise ValueError("o corpo não é um objeto")
        arranque, intervalo = corpo.get("arranque"), corpo.get("intervalo")
        ouvidos = corpo.get("ouvidos", [])
        if isinstance(arranque, bool) or not isinstance(arranque, int) \
                or not 1 <= arranque <= MAX_ARRANQUE:
            raise ValueError("'arranque' inválido")
        if isinstance(intervalo, bool) or not isinstance(intervalo, (int, float)) \
                or not 0.05 <= intervalo <= 60:
            raise ValueError("'intervalo' inválido")
        if not isinstance(ouvidos, list) or not all(
                isinstance(n, int) and not isinstance(n, bool) for n in ouvidos):
            raise ValueError("'ouvidos' inválido")
        return arranque, float(intervalo), ouvidos

    def _processar_hello(self, origem: int, corpo: dict | bytes, addr: Endereco) -> None:
        if (origem not in self._vizinhos_configurados and isinstance(corpo, dict)
                and corpo.get("socorro") is True and origem not in (0, self.transporte.id)):
            # Um nó que perdeu todos os vizinhos apresenta-se ao avô: passa a vizinho normal.
            self._vizinhos_configurados[origem] = addr
            self._vizinhos_estado[origem] = EstadoVizinho()
            self._emergencia.add(origem)
            self.transporte.set_address(origem, addr)
            log.info("[%s] pedido de socorro do nó %s (%s): aceite como vizinho",
                     self.transporte.id, origem, addr[0])
        esperado = self._vizinhos_configurados.get(origem)
        ev = self._vizinhos_estado.get(origem)
        if esperado is None or ev is None or addr != esperado:
            log.warning("[%s] HELLO de vizinho não configurado/inválido %s em %s",
                        self.transporte.id, origem, addr)
            return
        try:
            arranque, intervalo, ouvidos = self._validar_hello(corpo)
        except ValueError as exc:
            log.warning("[%s] corpo inválido em HELLO de %s: %s", self.transporte.id, origem, exc)
            return
        agora = time.monotonic()

        if ev.arranque is not None and arranque != ev.arranque and ev.estado in ("a ouvir", "vivo"):
            log.info("[%s] vizinho %s reiniciou (nº de arranque novo); estado antigo limpo",
                     self.transporte.id, origem)
            if ev.estado == "vivo":
                self._perder_vizinho(origem, "a ouvir")
            ev.proximo = agora  # responde já, para ele (de novo a arrancar) nos voltar a ouvir
        ev.arranque = arranque
        me_ouve = self.transporte.id in ouvidos

        if ev.estado == "vivo":
            self.transporte.set_intervalo(origem, intervalo)
            if not me_ouve:  # a ligação deixou de funcionar no sentido de cá para lá
                log.warning("[%s] vizinho %s deixou de me ouvir", self.transporte.id, origem)
                self._perder_vizinho(origem, "a ouvir")
                ev.proximo = agora  # responde já, para ele voltar a ouvir-me
            return

        if me_ouve:  # ouvem-se os dois: vivo
            voltou = ev.ja_vivo
            ev.estado, ev.ja_vivo, ev.rapidos = "vivo", True, HELLOS_RAPIDOS
            ev.vivo_desde = agora
            self.transporte.add_neighbor(origem, esperado)
            self.transporte.set_intervalo(origem, intervalo)
            ev.proximo = agora  # responde já: o HELLO dele pode ter vindo antes de ouvir o meu
            log.info("[%s] vizinho %s %s", self.transporte.id, origem, "voltou" if voltou else "ativo")
            self._registar_tabela()
        elif ev.estado != "a ouvir":
            ev.estado, ev.rapidos = "a ouvir", HELLOS_RAPIDOS
            ev.proximo = agora  # responde já, já com ele em "ouvidos"

    def _perder_vizinho(self, no: int, novo_estado: str, registar: bool = True) -> None:
        """O vizinho deixou de estar vivo (saiu, reiniciou ou deixou de me ouvir)."""
        ev = self._vizinhos_estado[no]
        ev.estado, ev.rapidos = novo_estado, HELLOS_RAPIDOS
        ev.proximo = time.monotonic() + HELLO_VIVO
        self.transporte.remove_neighbor(no)
        self._limpar_estado_vizinho(no)
        if registar:
            self._registar_tabela()

    def _vizinho_em_baixo(self, no: int) -> None:
        """O transporte deixou de ouvir o vizinho dentro do limite de silêncio."""
        ev = self._vizinhos_estado.get(no)
        if ev is not None:
            ev.estado, ev.rapidos = "em baixo", HELLOS_RAPIDOS
            ev.proximo = time.monotonic() + HELLO_VIVO
        self._limpar_estado_vizinho(no)
        log.warning("[%s] vizinho %s removido da tabela", self.transporte.id, no)
        if no in self._emergencia:
            self._esquecer_emergencia(no)
        self._registar_tabela()

    def _limpar_estado_vizinho(self, no: int) -> None:
        self._protegidos.discard(no)
        for estado in self.tabela_fluxos.values():
            estado.destinos.pop(no, None)
            if estado.pai == no:
                estado.pai = None
        self.rotas.vizinho_em_baixo(no)

    def _registar_tabela(self) -> None:
        vizinhos = [
            f"{no}@{self._vizinhos_configurados[no][0]}"
            for no in sorted(self.transporte.neighbors)
            if no in self._vizinhos_configurados
        ]
        log.info("[%s] tabela de vizinhos: %s", self.transporte.id,
                 ", ".join(vizinhos) if vizinhos else "(vazia)")
        for fluxo, estado in sorted(self.tabela_fluxos.items()):
            destinos = ", ".join(str(no) for no in sorted(estado.destinos)) or "(nenhum)"
            log.info("[%s] fluxo %s: pai=%s custo=%s destinos=%s ativo=%s",
                     self.transporte.id, fluxo, estado.pai, self._custo_texto(fluxo), destinos,
                     estado.ativo)

    def _custo_texto(self, fluxo: tuple[int, int]) -> str:
        """Custo até ao servidor do fluxo, como está agora (0 no próprio servidor)."""
        if self._servidor_local(fluxo):
            return "0"
        rota = self.rotas.rota(fluxo[0])
        return "-" if rota is None else f"{rota.custo:.1f}"


def portas_servidor(pedidas: Sequence[int | None]) -> list[int]:
    """Portas do Servidor.py local, uma por vídeo, a partir das opções `--servidor`.

    Um `--servidor` sem porta no k-ésimo lugar fica com PORTA_SERVIDOR + k (24000 para o vídeo 0,
    24001 para o vídeo 1, ...), como no Servidor.py. Dá ValueError com portas inválidas ou repetidas.
    """
    portas = [PORTA_SERVIDOR + k if p is None else p for k, p in enumerate(pedidas)]
    for porta in portas:
        if not 1 <= porta <= 65535:
            raise ValueError(f"porta de --servidor inválida: {porta}")
    if len(set(portas)) != len(portas):
        raise ValueError(f"portas de --servidor repetidas: {portas}")
    return portas


def endereco_bootstrapper(texto: str, porta: int) -> tuple[str, int]:
    """`10.0.21.20` ou `10.0.21.20:5001` (a porta no endereço ganha à de --porta)."""
    if texto.count(":") == 1:  # IPv4:porta (um IPv6 tem vários ':')
        texto, porta_texto = texto.split(":")
        porta = int(porta_texto)
    ipaddress.ip_address(texto)
    if not 1 <= porta <= 65535:
        raise ValueError("as portas têm de estar entre 1 e 65535")
    return texto, porta


def nome_do_no() -> str:
    """Nome que aparece em cada linha do log: OTT_NOME, ou o nome da máquina (no CORE, o do nó)."""
    nome = os.environ.get("OTT_NOME") or socket.gethostname()
    return nome.replace("%", "").strip()


def sessao_core(nome: str) -> str | None:
    """Número da sessão do CORE em que este nó corre: a /tmp/pycore.<n> que tem a pasta <nó>.conf.
    No CORE, os nós partilham o disco do PC, por isso isto também se vê de dentro de um nó."""
    candidatas = [p for p in Path("/tmp").glob("pycore.*") if (p / f"{nome}.conf").is_dir()]
    if not candidatas:
        return None
    return max(candidatas, key=lambda p: p.stat().st_mtime).name.split(".", 1)[1]


def ficheiro_de_log(nome: str, pedido: str | None) -> Path | None:
    """Onde o nó guarda também o seu log, para o painel o ler (python3 scripts/painel.py).

    --log FICHEIRO ganha a tudo. Arrancado à mão numa shell do CORE: logs/core-<sessão>/<nó>.log.
    Com a saída já a ir para um ficheiro (o arrancar_cenario.sh faz isso), não escreve outro."""
    if pedido:
        return Path(pedido)
    if not nome or not sys.stderr.isatty():
        return None
    sessao = sessao_core(nome)
    return None if sessao is None else PROJETO / "logs" / f"core-{sessao}" / f"{nome}.log"


COMANDOS_SAIR = ("sair", "q")


async def correr(no: NoOTT, videos: list, abrir_player: bool, nome: str,
                 log_player: Path | None = None) -> None:
    """Arranca o nó e, depois do registo, os programas locais (servidores de vídeo e player).

    Duas formas de fechar o nó:
    * Ctrl+C (SIGINT): o nó morre logo, sem avisar ninguém, como numa falha (pedido do professor).
      Os vizinhos dão por isso pela falta de vídeo ou pelo silêncio dos HELLOs.
    * 'sair' + Enter no terminal do nó, ou `kill <pid>` (SIGTERM): saída avisada, com CLOSE.
    Os programas que o nó arrancou morrem com ele nos dois casos (ver ott/locais.py)."""
    def escrever(linha: str) -> None:  # linhas do player: no terminal e, se houver, no ficheiro
        print(linha, flush=True)
        if log_player is not None:
            try:
                with log_player.open("a", encoding="utf-8") as f:
                    f.write(linha + "\n")
            except OSError:
                pass

    loop = asyncio.get_running_loop()
    sair = asyncio.Event()

    def falhar() -> None:
        log.warning("Ctrl+C: o nó termina já, sem avisar os vizinhos (como uma falha)")
        logging.shutdown()
        os._exit(130)

    def ler_comando() -> None:
        linha = sys.stdin.readline()
        if not linha:  # o terminal fechou: deixa de ler
            loop.remove_reader(sys.stdin.fileno())
        elif linha.strip().lower() in COMANDOS_SAIR:
            sair.set()
        elif linha.strip():
            log.info("comando desconhecido: %r (para sair a avisar os vizinhos: 'sair')",
                     linha.strip())

    loop.add_signal_handler(signal.SIGINT, falhar)
    loop.add_signal_handler(signal.SIGTERM, sair.set)
    if sys.stdin.isatty():
        loop.add_reader(sys.stdin.fileno(), ler_comando)
    locais = ProgramasLocais(nome, escrever)
    try:
        await no.start(insistir=True)
        if videos:
            await locais.servidores(videos)
        if abrir_player:
            await locais.player(no.transporte.id, ao_fechar=no.parar_de_ver)
        log.info("para sair avisando os vizinhos: 'sair' + Enter; Ctrl+C mata o nó sem avisar")
        await sair.wait()
        log.info("a sair: avisa os vizinhos (CLOSE)")
    finally:
        await locais.fechar()
        await no.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Arranca um nó OTT e regista-o no bootstrapper",
        epilog="Exemplos (cenário 3): 'python3 -m ott 10.0.21.20:5001' (nó intermédio); "
               "'... --videos movie movie2' (servidor); '... --ver 0' (cliente, abre o player).")
    parser.add_argument("ip_bootstrapper", help="IP do bootstrapper, opcionalmente com a porta "
                        "(ex: 10.0.21.20:5001)")
    parser.add_argument("--porta", type=int, default=PORTA, help=f"porta UDP do bootstrapper "
                        f"(predefinição: {PORTA})")
    parser.add_argument("--porta-local", type=int, default=PORTA,
                        help=f"porta UDP local (predefinição: {PORTA})")
    parser.add_argument("--videos", nargs="+", default=[], metavar="VIDEO",
                        help="este nó é servidor: arranca um Servidor.py por vídeo (ex: movie "
                             "movie2, procurados na pasta videos/); o k-ésimo é o vídeo k")
    parser.add_argument("--servidor", type=int, action="append", metavar="PORTA", nargs="?",
                        const=None, default=[],
                        help="este nó tem servidor(es) local(is) já a correr: escuta o Servidor.py "
                             f"em localhost nesta porta (sem valor: {PORTA_SERVIDOR} + k para o "
                             "vídeo k); repetir para mais vídeos (o k-ésimo é o vídeo k)")
    parser.add_argument("--player", type=int, nargs="?", const=PORTA_PLAYER, default=None,
                        metavar="PORTA",
                        help="entrega o vídeo a um player já aberto em localhost, sem abrir outro "
                             f"(sem valor: {PORTA_PLAYER})")
    parser.add_argument("--ver", action="append", default=[], metavar="[SERVIDOR:]VIDEO",
                        help="vídeo a pedir e mostrar: 'k' ao servidor mais barato, "
                             "'servidor:k' a esse servidor; ex: 0 ou 1:0. Sem --player, o nó abre "
                             "o player sozinho")
    parser.add_argument("--log", metavar="FICHEIRO",
                        help="guarda também o log neste ficheiro (por omissão, numa shell do CORE: "
                             "logs/core-<sessão>/<nó>.log, que o scripts/painel.py lê)")
    args = parser.parse_args()
    try:
        ip_boot, porta_boot = endereco_bootstrapper(args.ip_bootstrapper, args.porta)
        if not 1 <= args.porta_local <= 65535:
            parser.error("as portas têm de estar entre 1 e 65535")
        if args.videos and args.servidor:
            parser.error("usar --videos (o nó arranca os servidores) ou --servidor (já a correr), "
                         "não os dois")
        videos = [ficheiro_video(v) for v in args.videos]
        args.servidor = portas_servidor([None] * len(videos) if videos else args.servidor)
    except ValueError as exc:
        parser.error(str(exc))

    # O nome do nó vai em cada linha do log (no CORE, o nome da máquina é o do nó: N1, C1, ...).
    nome = nome_do_no()
    formato = logging.Formatter(
        "%(asctime)s.%(msecs)03d " + (f"{nome} " if nome else "") + "%(levelname)s %(message)s",
        datefmt="%H:%M:%S")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().handlers[0].setFormatter(formato)
    ficheiro = ficheiro_de_log(nome, args.log)
    log_player = None
    if ficheiro is not None:
        try:
            ficheiro.parent.mkdir(parents=True, exist_ok=True)
            guardar = logging.FileHandler(ficheiro, encoding="utf-8")
            guardar.setFormatter(formato)
            logging.getLogger().addHandler(guardar)
            log_player = ficheiro.with_name(f"{ficheiro.stem}-player.log")
            log.info("log guardado em %s", ficheiro)
        except OSError as exc:
            log.warning("não consegui guardar o log em %s: %s", ficheiro, exc)
    try:
        fluxos, videos_pedidos = set(), set()
        for v in args.ver:
            if ":" not in v:
                video = int(v)
                if not 0 <= video <= 255:
                    raise ValueError(f"vídeo inválido: {v}")
                videos_pedidos.add(video)
                continue
            servidor, stream = v.split(":")
            fluxo = (int(servidor), int(stream))
            NoOTT._validar_fluxo(fluxo)
            fluxos.add(fluxo)
        abrir_player = bool(args.ver) and args.player is None
        porta_player = PORTA_PLAYER if abrir_player else args.player
        no = NoOTT(ip_boot, porta_bootstrapper=porta_boot, porta=args.porta_local,
                   portas_servidor=args.servidor,
                   player=None if porta_player is None else (LOCALHOST, porta_player))
        no.fluxos_player = fluxos
        no.videos_pedidos = videos_pedidos
        asyncio.run(correr(no, videos, abrir_player, nome, log_player))
        log.info("nó OTT terminado")
    except KeyboardInterrupt:  # Ctrl+C antes de o ciclo do nó arrancar
        log.info("nó OTT terminado")
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    main()
