"""Formato dos pacotes do protocolo OTT ("QUIC-like" sobre UDP).

Especificação completa em docs/protocolo.md. Resumo:

    cabeçalho fixo (14 B, ordem de rede, struct "!BBBHHIHB")
        versão(1) tipo(1) flags(1) origem(2) destino(2) nº pacote(4)
        fluxo: servidor(2) fluxo: stream(1)
    [id da mensagem (4 B)]   só se a flag FIAVEL estiver ligada
    corpo                    JSON nas mensagens de controlo, bytes crus no DATA
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum

VERSAO = 1
PORTA = 5000  # porta UDP de todos os nós no CORE (cada nó tem o seu namespace)

CABECALHO = struct.Struct("!BBBHHIHB")  # 14 bytes
ID_MSG = struct.Struct("!I")  # 4 bytes, só nos pacotes fiáveis
CORPO_ACK = struct.Struct("!I")  # o ACK leva o nº do pacote confirmado

SEM_ID = 0  # nó que ainda não se registou (e ID do bootstrapper)
BOOTSTRAPPER = 0
FLUXO_CONTROLO = (0, 0)


class Tipo(IntEnum):
    REGISTER = 0x01
    REGISTER_ACK = 0x02
    HELLO = 0x03
    HELLO_ACK = 0x04
    KEEPALIVE = 0x05
    ACK = 0x06
    CLOSE = 0x07
    # 0x10–0x1F: reservados para as rotas (Etapa 2)
    DATA = 0x20


class Flag:
    FIAVEL = 0x01  # o recetor tem de responder com ACK


@dataclass
class Pacote:
    tipo: int
    origem: int
    destino: int
    pn: int
    fluxo: tuple[int, int] = FLUXO_CONTROLO
    flags: int = 0
    id_msg: int | None = None
    corpo: bytes = b""

    @property
    def fiavel(self) -> bool:
        return bool(self.flags & Flag.FIAVEL)

    def codificar(self) -> bytes:
        dados = CABECALHO.pack(
            VERSAO, self.tipo, self.flags, self.origem, self.destino,
            self.pn, self.fluxo[0], self.fluxo[1],
        )
        if self.fiavel:
            dados += ID_MSG.pack(self.id_msg)
        return dados + self.corpo

    @classmethod
    def descodificar(cls, dados: bytes) -> "Pacote":
        if len(dados) < CABECALHO.size:
            raise ValueError(f"pacote com {len(dados)} B, menor que o cabeçalho")
        versao, tipo, flags, origem, destino, pn, f_srv, f_stream = CABECALHO.unpack_from(dados)
        if versao != VERSAO:
            raise ValueError(f"versão {versao} desconhecida")
        pos = CABECALHO.size
        id_msg = None
        if flags & Flag.FIAVEL:
            if len(dados) < pos + ID_MSG.size:
                raise ValueError("pacote fiável sem id da mensagem")
            (id_msg,) = ID_MSG.unpack_from(dados, pos)
            pos += ID_MSG.size
        return cls(tipo, origem, destino, pn, (f_srv, f_stream), flags, id_msg, dados[pos:])


def nome_tipo(tipo: int) -> str:
    try:
        return Tipo(tipo).name
    except ValueError:
        return f"0x{tipo:02x}"


def codificar_corpo(corpo: dict | bytes | None) -> bytes:
    """Controlo (dict) vai em JSON; vídeo (bytes) vai cru."""
    if corpo is None:
        return b""
    if isinstance(corpo, (bytes, bytearray)):
        return bytes(corpo)
    return json.dumps(corpo, separators=(",", ":")).encode()


def descodificar_corpo(tipo: int, dados: bytes) -> dict | bytes:
    if tipo == Tipo.DATA:
        return dados
    return json.loads(dados) if dados else {}
