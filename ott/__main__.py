"""Arranque de um nó OTT através do bootstrapper."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging

from typing import Sequence

from .protocolo import PORTA, Tipo, nome_tipo
from .transporte import Endereco, Transporte
from .video import LOCALHOST, PORTA_PLAYER, PORTA_SERVIDOR, PontaVideo

log = logging.getLogger("ott")


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

    async def run(self) -> None:
        try:
            await self.start()
            await asyncio.Event().wait()
        finally:
            self.close()

    def close(self) -> None:
        self.video.close()
        self.transporte.close()

    def _receber(self, origem: int, tipo: int, corpo: dict | bytes,
                 fluxo: tuple[int, int], addr: Endereco) -> None:
        if not self._registado:
            if tipo == Tipo.REGISTER_ACK:
                self._processar_registo(origem, corpo, addr)
            return

        if tipo in (Tipo.HELLO, Tipo.HELLO_ACK):
            self._processar_handshake(origem, tipo, corpo, addr)
            return

        if tipo == Tipo.DATA and isinstance(corpo, bytes):
            self._distribuir_video(fluxo, corpo)
            return

        log.warning("[%s] mensagem %s inesperada de %s", self.transporte.id,
                    nome_tipo(tipo), addr)

    # ------------------------------------------------------------ vídeo
    def _frame_do_servidor(self, stream: int, rtp: bytes) -> None:
        """Pacote RTP vindo do Servidor.py local: passa a ser o fluxo (meu ID, stream)."""
        if not self._registado:
            return  # ainda sem ID, não há fluxo para nomear
        fluxo = (self.transporte.id, stream)
        for destino in self.destinos_locais:
            self.rotas_video.setdefault(fluxo, set()).add(destino)
        self._distribuir_video(fluxo, rtp)

    def _distribuir_video(self, fluxo: tuple[int, int], rtp: bytes) -> None:
        """Entrega o pacote ao player local e/ou reenvia-o aos vizinhos a jusante."""
        if fluxo in self.fluxos_player:
            self.video.entregar_ao_player(rtp)
        for vizinho in self.rotas_video.get(fluxo, ()):
            if vizinho in self.transporte.neighbors:
                self.transporte.send(vizinho, Tipo.DATA, rtp, fluxo=fluxo)  # sem fiabilidade

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

        voltou = origem in self._caidos
        if origem not in self.transporte.neighbors:
            self.transporte.add_neighbor(origem, esperado)
        self._caidos.discard(origem)
        primeira_confirmacao = origem not in self._confirmados
        self._confirmados.add(origem)

        if tipo == Tipo.HELLO:
            self.transporte.hello_ack(origem)

        if voltou:
            log.info("[%s] vizinho %s voltou", self.transporte.id, origem)
        elif primeira_confirmacao:
            log.info("[%s] vizinho %s ativo", self.transporte.id, origem)
        if voltou or primeira_confirmacao:
            self._registar_tabela()

    def _vizinho_em_baixo(self, no: int) -> None:
        self._confirmados.discard(no)
        self._caidos.add(no)
        log.warning("[%s] vizinho %s removido da tabela", self.transporte.id, no)
        self._registar_tabela()

    def _registar_tabela(self) -> None:
        vizinhos = [
            f"{no}@{self._vizinhos_configurados[no][0]}"
            for no in sorted(self.transporte.neighbors)
            if no in self._vizinhos_configurados
        ]
        log.info("[%s] tabela de vizinhos: %s", self.transporte.id,
                 ", ".join(vizinhos) if vizinhos else "(vazia)")


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
