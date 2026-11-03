"""Arranque de um nó OTT através do bootstrapper."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging

from .protocolo import PORTA, Tipo, nome_tipo
from .transporte import Endereco, Transporte

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

    async def start(self, timeout: float = 15) -> None:
        self._registo = asyncio.get_running_loop().create_future()
        await self.transporte.start(self.host, self.porta)
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
        self.transporte.close()

    def _receber(self, origem: int, tipo: int, corpo: dict | bytes,
                 _fluxo: tuple[int, int], addr: Endereco) -> None:
        if not self._registado:
            if tipo == Tipo.REGISTER_ACK:
                self._processar_registo(origem, corpo, addr)
            return

        if tipo in (Tipo.HELLO, Tipo.HELLO_ACK):
            self._processar_handshake(origem, tipo, corpo, addr)
            return

        log.warning("[%s] mensagem %s inesperada de %s", self.transporte.id,
                    nome_tipo(tipo), addr)

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
        asyncio.run(NoOTT(args.ip_bootstrapper, porta_bootstrapper=args.porta,
                          porta=args.porta_local).run())
    except KeyboardInterrupt:
        log.info("nó OTT terminado")
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    main()
