"""Bootstrapper UDP da rede overlay."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ott.protocolo import Tipo, nome_tipo
from ott.transporte import Endereco, Transporte

log = logging.getLogger("bootstrapper")


@dataclass(frozen=True)
class Configuracao:
    host: str
    porta: int
    ids_por_ip: dict[str, int]
    vizinhos_por_id: dict[int, list[dict[str, int | str]]]


def _inteiro(valor: Any, campo: str, minimo: int, maximo: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        raise ValueError(f"'{campo}' deve ser um inteiro entre {minimo} e {maximo}")
    return valor


def _ip(valor: Any, campo: str) -> str:
    if not isinstance(valor, str):
        raise ValueError(f"'{campo}' deve ser um endereço IP")
    try:
        ipaddress.ip_address(valor)
    except ValueError as exc:
        raise ValueError(f"'{campo}' não é um endereço IP válido: {valor!r}") from exc
    return valor


def carregar_configuracao(caminho: str | Path) -> Configuracao:
    """Lê e valida a configuração do bootstrapper e da topologia overlay."""
    caminho = Path(caminho)
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"não foi possível ler a configuração '{caminho}': {exc}") from exc
    if not isinstance(dados, dict):
        raise ValueError("a raiz da configuração deve ser um objeto JSON")

    bootstrapper = dados.get("bootstrapper")
    if not isinstance(bootstrapper, dict):
        raise ValueError("'bootstrapper' deve ser um objeto")
    host = _ip(bootstrapper.get("host"), "bootstrapper.host")
    porta = _inteiro(bootstrapper.get("port"), "bootstrapper.port", 1, 65535)

    nos = dados.get("nodes")
    if not isinstance(nos, dict) or not nos:
        raise ValueError("'nodes' deve ser um objeto não vazio de IP para ID")
    ids_por_ip: dict[str, int] = {}
    for endereco, identificador in nos.items():
        endereco = _ip(endereco, "nodes.<ip>")
        ids_por_ip[endereco] = _inteiro(identificador, f"nodes.{endereco}", 1, 65535)

    ligacoes = dados.get("links")
    if not isinstance(ligacoes, list):
        raise ValueError("'links' deve ser uma lista")
    vizinhos_por_id: dict[int, list[dict[str, int | str]]] = {}
    for indice, ligacao in enumerate(ligacoes):
        campo = f"links[{indice}]"
        if not isinstance(ligacao, dict):
            raise ValueError(f"'{campo}' deve ser um objeto")
        no1 = _inteiro(ligacao.get("node1"), f"{campo}.node1", 1, 65535)
        no2 = _inteiro(ligacao.get("node2"), f"{campo}.node2", 1, 65535)
        ip1 = _ip(ligacao.get("ip1"), f"{campo}.ip1")
        ip2 = _ip(ligacao.get("ip2"), f"{campo}.ip2")
        if no1 == no2:
            raise ValueError(f"'{campo}' não pode ligar um nó a si próprio")
        if ids_por_ip.get(ip1) != no1 or ids_por_ip.get(ip2) != no2:
            raise ValueError(f"'{campo}' tem IDs/IPs que não correspondem a 'nodes'")

        for origem, destino, endereco in ((no1, no2, ip2), (no2, no1, ip1)):
            vizinhos = vizinhos_por_id.setdefault(origem, [])
            if any(vizinho["id"] == destino for vizinho in vizinhos):
                raise ValueError(f"'{campo}' duplica a ligação entre os nós {origem} e {destino}")
            vizinhos.append({"id": destino, "ip": endereco})

    return Configuracao(host, porta, ids_por_ip, vizinhos_por_id)


class Bootstrapper:
    def __init__(self, configuracao: Configuracao, *, host: str | None = None,
                 porta: int | None = None):
        self.configuracao = configuracao
        self.host = host or configuracao.host
        self.porta = configuracao.porta if porta is None else porta
        self.transporte = Transporte(meu_id=0, on_message=self._receber)

    async def start(self) -> None:
        await self.transporte.start(self.host, self.porta)
        log.info("bootstrapper à escuta em %s:%d", self.host, self.endereco_local[1])

    @property
    def endereco_local(self) -> Endereco:
        return self.transporte.endereco_local

    def close(self) -> None:
        self.transporte.close()

    def _receber(self, origem: int, tipo: int, corpo: dict | bytes,
                 _fluxo: tuple[int, int], addr: Endereco) -> None:
        del _fluxo
        if tipo != Tipo.REGISTER:
            log.warning("mensagem %s inesperada de %s; ignorada", nome_tipo(tipo), addr)
            return
        if origem != 0 or corpo != {}:
            log.warning("REGISTER inválido de %s (origem=%d, corpo=%r); ignorado",
                        addr, origem, corpo)
            return

        identificador = self.configuracao.ids_por_ip.get(addr[0])
        if identificador is None:
            log.warning("IP não configurado tentou registar-se: %s", addr[0])
            return

        vizinhos = self.configuracao.vizinhos_por_id.get(identificador, [])
        self.transporte.set_address(0, addr)
        self.transporte.send(
            0,
            Tipo.REGISTER_ACK,
            {"id": identificador, "vizinhos": vizinhos},
            fiavel=True,
        )
        log.info("registo de %s: nó %d, %d vizinho(s)",
                 addr[0], identificador, len(vizinhos))

    async def serve(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            self.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrapper da rede overlay")
    parser.add_argument("config", help="ficheiro JSON com os nós e as ligações")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    configuracao = carregar_configuracao(args.config)
    try:
        asyncio.run(Bootstrapper(configuracao).serve())
    except KeyboardInterrupt:
        log.info("bootstrapper terminado")
