"""Arranque de um nó OTT através do bootstrapper."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
import time
from typing import Sequence

from .protocolo import PORTA, Tipo, nome_tipo
from .transporte import Endereco, Transporte
from .video import LOCALHOST, PORTA_PLAYER, PORTA_SERVIDOR, PontaVideo

log = logging.getLogger("ott")

RENOVACAO_JOIN = 2.0
VALIDADE_DESTINO = 6.0
PERIODO_PING = 5.0
INTERVALOS_HELLO = (5, 10, 20, 40, 60)


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
        self._confirmados: set[int] = set()
        self._caidos: set[int] = set()
        self._registado = False
        # Vídeo: ligações locais (UDP em localhost) ao servidor e ao player.
        # `fluxos_player` e `destinos_locais` são provisórios: na Etapa 2 passam a ser
        # preenchidos pelas rotas (JOIN / anúncios) em vez de configurados à mão.
        self.video = PontaVideo(self._frame_do_servidor, portas_servidor, player, host=host_video)
        self.fluxos_player: set[tuple[int, int]] = set()  # fluxos entregues ao player local
        self.destinos_locais: set[int] = set()  # vizinhos a quem enviar o vídeo do servidor local
        self.rotas_video: dict[tuple[int, int], set[int]] = {}  # fluxo -> vizinhos a jusante
        self.tabela_fluxos: dict[tuple[int, int], FluxoVideo] = {}
        self.pais_video: dict[tuple[int, int], int] = {}
        self._hellos_recebidos: set[int] = set()
        self._tarefas_religacao: dict[int, asyncio.Task] = {}
        self._tarefas_aplicacao: list[asyncio.Task] = []
        self._ping_seq: dict[tuple[int, int], int] = {}
        self._ultima_renovacao: dict[tuple[int, int], float] = {}
        self._em_encerramento = False

    async def start(self, timeout: float = 15) -> None:
        self._registo = asyncio.get_running_loop().create_future()
        await self.transporte.start(self.host, self.porta)
        await self.video.start()
        self.transporte.set_address(0, self.endereco_bootstrapper)
        self.transporte.register()
        try:
            await asyncio.wait_for(self._registo, timeout)
        except asyncio.TimeoutError as exc:
            self.close()
            raise RuntimeError(
                f"sem resposta do bootstrapper {self.endereco_bootstrapper[0]}:"
                f"{self.endereco_bootstrapper[1]} após {timeout:g} segundos"
            ) from exc
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
                    self.transporte.send(vizinho, Tipo.CLOSE, {}, fiavel=True)
                )
        limite = asyncio.get_running_loop().time() + max(0.0, timeout)
        while mensagens and asyncio.get_running_loop().time() < limite:
            if not any(msg in self.transporte._pendentes for msg in mensagens):
                break
            await asyncio.sleep(0.01)
        self.video.close()
        self.transporte.close()

    def _cancelar_tarefas(self) -> None:
        for tarefa in self._tarefas_religacao.values():
            tarefa.cancel()
        self._tarefas_religacao.clear()
        for tarefa in self._tarefas_aplicacao:
            tarefa.cancel()
        self._tarefas_aplicacao.clear()

    def _receber(self, origem: int, tipo: int, corpo: dict | bytes,
                 fluxo: tuple[int, int], addr: Endereco) -> None:
        if not self._registado:
            if tipo == Tipo.REGISTER_ACK:
                self._processar_registo(origem, corpo, addr)
            return

        if tipo in (Tipo.HELLO, Tipo.HELLO_ACK):
            self._processar_handshake(origem, tipo, corpo, addr)
            return

        if tipo == Tipo.CLOSE:
            if corpo != {}:
                log.warning("[%s] corpo inválido em CLOSE de %s", self.transporte.id, origem)
                return
            if origem not in self._confirmados:
                return
            self.transporte.remove_neighbor(origem)
            self._vizinho_em_baixo(origem)
            log.info("[%s] vizinho %s avisou que vai sair", self.transporte.id, origem)
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

        if tipo == Tipo.DATA and isinstance(corpo, bytes):
            self._receber_video(origem, fluxo, corpo)
            return

        log.warning("[%s] mensagem %s inesperada de %s", self.transporte.id,
                    nome_tipo(tipo), addr)

    # ------------------------------------------------------------ vídeo
    def _frame_do_servidor(self, stream: int, rtp: bytes) -> None:
        """Pacote RTP vindo do Servidor.py local: passa a ser o fluxo (meu ID, stream)."""
        if not self._registado:
            return  # ainda sem ID, não há fluxo para nomear
        fluxo = (self.transporte.id, stream)
        self._sincronizar_destinos_locais(fluxo)
        if self._ativo(fluxo):
            self._distribuir_video(fluxo, rtp)

    def _distribuir_video(self, fluxo: tuple[int, int], rtp: bytes) -> None:
        """Entrega o pacote ao player local e/ou reenvia-o aos vizinhos a jusante."""
        if fluxo in self.fluxos_player:
            self.video.entregar_ao_player(rtp)
        for vizinho in self.rotas_video.get(fluxo, ()):
            if vizinho in self.transporte.neighbors:
                self.transporte.send(vizinho, Tipo.DATA, rtp, fluxo=fluxo)  # sem fiabilidade

    def pedir_video(self, fluxo: tuple[int, int]) -> None:
        """Regista o interesse do player local; o JOIN sobe quando houver pai."""
        self._validar_fluxo(fluxo)
        self.fluxos_player.add(fluxo)
        estado = self._estado_fluxo(fluxo)
        estado.local = True
        pai = self.pais_video.get(fluxo)
        if self._registado and pai is not None:
            self._enviar_join(fluxo, pai, fiavel=True)

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
        if pai is None:
            self.pais_video.pop(fluxo, None)
        else:
            self.pais_video[fluxo] = pai
            if self._registado and self._ativo(fluxo) and not self._servidor_local(fluxo):
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

    def _estado_fluxo(self, fluxo: tuple[int, int]) -> FluxoVideo:
        estado = self.tabela_fluxos.setdefault(fluxo, FluxoVideo())
        estado.local = fluxo in self.fluxos_player
        pai = self.pais_video.get(fluxo)
        if estado.pai is None and pai is not None:
            estado.pai = pai
        return estado

    def _ativo(self, fluxo: tuple[int, int]) -> bool:
        estado = self.tabela_fluxos.get(fluxo)
        return bool(estado and estado.ativo)

    def _servidor_local(self, fluxo: tuple[int, int]) -> bool:
        return (
            self._registado
            and fluxo[0] == self.transporte.id
            and 0 <= fluxo[1] < len(self.video.portas_pedidas)
        )

    def _sincronizar_destinos_locais(self, fluxo: tuple[int, int]) -> None:
        estado = self._estado_fluxo(fluxo)
        destinos = self.rotas_video.setdefault(fluxo, set())
        for vizinho in self.destinos_locais:
            if vizinho in self._vizinhos_configurados:
                estado.destinos.setdefault(vizinho, None)
                destinos.add(vizinho)

    def _receber_join(self, origem: int, fluxo: tuple[int, int]) -> None:
        if origem not in self._confirmados or origem == self.pais_video.get(fluxo):
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
        self.rotas_video.setdefault(fluxo, set()).add(origem)
        if not ja_ativo:
            self._propagar_estado_fluxo(fluxo)
        self._registar_tabela()

    def _receber_leave(self, origem: int, fluxo: tuple[int, int]) -> None:
        estado = self.tabela_fluxos.get(fluxo)
        if origem not in self._confirmados or estado is None:
            return
        estado.destinos.pop(origem, None)
        self.rotas_video.get(fluxo, set()).discard(origem)
        self._propagar_estado_fluxo(fluxo)
        self._registar_tabela()

    def _propagar_estado_fluxo(self, fluxo: tuple[int, int]) -> None:
        pai = self.pais_video.get(fluxo)
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
        if origem != self.pais_video.get(fluxo) or not self._ativo(fluxo):
            log.debug("[%s] DATA de %s para fluxo %s descartado", self.transporte.id,
                      origem, fluxo)
            return
        self._distribuir_video(fluxo, rtp)

    def _receber_ping(self, origem: int, fluxo: tuple[int, int], corpo: dict) -> None:
        if origem != self.pais_video.get(fluxo) or not self._ativo(fluxo):
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
        for destino in self.rotas_video.get(fluxo, ()):
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

    async def _ciclo_fluxos(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            agora = time.monotonic()
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
                    self.rotas_video.get(fluxo, set()).discard(vizinho)
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
                for destino in self.rotas_video.get(fluxo, ()):
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
            for no, endereco in vizinhos.items():
                self.transporte.add_neighbor(no, endereco)
                self.transporte.hello(no)
            for fluxo in self.fluxos_player:
                self._estado_fluxo(fluxo)
            for stream in range(len(self.video.portas_pedidas)):
                fluxo = (identificador, stream)
                self._estado_fluxo(fluxo)
                self._sincronizar_destinos_locais(fluxo)
            log.info("[%s] registo aceite; vizinhos configurados: %d",
                     identificador, len(vizinhos))
            self._registar_tabela()
            self._registo.set_result(None)
        except (ValueError, TypeError) as exc:
            self._registo.set_exception(ValueError(f"REGISTER_ACK inválido: {exc}"))

    def _processar_handshake(self, origem: int, tipo: int, corpo: dict | bytes,
                             addr: Endereco) -> None:
        esperado = self._vizinhos_configurados.get(origem)
        if esperado is None or addr != esperado:
            log.warning("[%s] %s de vizinho não configurado/inválido %s em %s",
                        self.transporte.id, nome_tipo(tipo), origem, addr)
            return
        if corpo != {}:
            log.warning("[%s] corpo inválido em %s de %s: %r",
                        self.transporte.id, nome_tipo(tipo), origem, corpo)
            return

        reiniciou = (
            tipo == Tipo.HELLO
            and origem in self._confirmados
            and origem in self._hellos_recebidos
        )
        if reiniciou:
            self._limpar_estado_vizinho(origem)
            self._registar_tabela()
            log.info("[%s] HELLO repetido de %s; estado antigo limpo (reinício)",
                     self.transporte.id, origem)

        voltou = origem in self._caidos
        if origem not in self.transporte.neighbors:
            self.transporte.add_neighbor(origem, esperado)
        self._caidos.discard(origem)
        primeira_confirmacao = origem not in self._confirmados
        self._confirmados.add(origem)

        if tipo == Tipo.HELLO:
            self._hellos_recebidos.add(origem)
            self.transporte.hello_ack(origem)
        self._cancelar_religacao(origem)

        if voltou:
            log.info("[%s] vizinho %s voltou", self.transporte.id, origem)
        elif primeira_confirmacao:
            log.info("[%s] vizinho %s ativo", self.transporte.id, origem)
        if voltou or primeira_confirmacao:
            self._registar_tabela()

    def _vizinho_em_baixo(self, no: int) -> None:
        self._confirmados.discard(no)
        self._caidos.add(no)
        self._hellos_recebidos.discard(no)
        self._limpar_estado_vizinho(no)
        log.warning("[%s] vizinho %s removido da tabela", self.transporte.id, no)
        self._registar_tabela()
        if not self._em_encerramento:
            self._agendar_religacao(no)

    def _limpar_estado_vizinho(self, no: int) -> None:
        for fluxo, estado in self.tabela_fluxos.items():
            estado.destinos.pop(no, None)
            self.rotas_video.get(fluxo, set()).discard(no)
            if estado.pai == no:
                estado.pai = None
                self.pais_video.pop(fluxo, None)

    def _agendar_religacao(self, no: int) -> None:
        tarefa = self._tarefas_religacao.get(no)
        if tarefa is None or tarefa.done():
            self._tarefas_religacao[no] = asyncio.create_task(self._tentar_religacao(no))

    def _cancelar_religacao(self, no: int) -> None:
        tarefa = self._tarefas_religacao.pop(no, None)
        if tarefa is not None:
            tarefa.cancel()

    async def _tentar_religacao(self, no: int) -> None:
        tentativa = 0
        try:
            while no in self._caidos and not self._em_encerramento:
                espera = INTERVALOS_HELLO[min(tentativa, len(INTERVALOS_HELLO) - 1)]
                await asyncio.sleep(espera)
                if no not in self._caidos or self._em_encerramento:
                    return
                try:
                    self.transporte.hello(no)
                except ValueError:
                    log.warning("[%s] sem endereço para tentar HELLO ao vizinho %s",
                                self.transporte.id, no)
                    return
                tentativa += 1
        except asyncio.CancelledError:
            raise
        finally:
            if self._tarefas_religacao.get(no) is asyncio.current_task():
                self._tarefas_religacao.pop(no, None)

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
            log.info("[%s] fluxo %s: pai=%s destinos=%s ativo=%s",
                     self.transporte.id, fluxo, estado.pai, destinos, estado.ativo)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arranca um nó OTT e regista-o no bootstrapper")
    parser.add_argument("ip_bootstrapper", help="IP do bootstrapper")
    parser.add_argument("--porta", type=int, default=PORTA, help=f"porta UDP (predefinição: {PORTA})")
    parser.add_argument("--porta-local", type=int, default=PORTA,
                        help=f"porta UDP local (predefinição: {PORTA})")
    parser.add_argument("--servidor", type=int, action="append", metavar="PORTA", nargs="?",
                        const=PORTA_SERVIDOR, default=[],
                        help="este nó tem servidor(es) local(is): escuta o Servidor.py em localhost "
                             f"nesta porta (sem valor: {PORTA_SERVIDOR}); repetir para mais vídeos "
                             "(o k-ésimo é o vídeo k)")
    parser.add_argument("--player", type=int, nargs="?", const=PORTA_PLAYER, default=None,
                        metavar="PORTA",
                        help=f"entrega o vídeo ao player em localhost (sem valor: {PORTA_PLAYER})")
    parser.add_argument("--ver", action="append", default=[], metavar="SERVIDOR:STREAM",
                        help="(provisório, até haver rotas) fluxo a entregar ao player; ex: 1:0")
    parser.add_argument("--enviar-para", type=int, action="append", default=[], metavar="ID",
                        help="(provisório, até haver rotas) vizinho a quem enviar o vídeo do servidor local")
    args = parser.parse_args()
    try:
        ipaddress.ip_address(args.ip_bootstrapper)
        if not 1 <= args.porta <= 65535 or not 1 <= args.porta_local <= 65535:
            parser.error("as portas têm de estar entre 1 e 65535")
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        fluxos = set()
        for v in args.ver:
            servidor, stream = v.split(":")
            fluxos.add((int(servidor), int(stream)))
        no = NoOTT(args.ip_bootstrapper, porta_bootstrapper=args.porta, porta=args.porta_local,
                   portas_servidor=args.servidor,
                   player=None if args.player is None else (LOCALHOST, args.player))
        no.fluxos_player = fluxos
        no.destinos_locais = set(args.enviar_para)
        asyncio.run(no.run())
    except KeyboardInterrupt:
        log.info("nó OTT terminado")
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    main()
