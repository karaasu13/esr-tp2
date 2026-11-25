"""Lê a topologia do CORE de um cenário (topologias/cenarioN.xml): nome, tipo, posição no ecrã e
IPs de cada máquina, e as ligações da rede real. Serve o painel e o desenho da árvore, que assim
usam os mesmos nomes e as mesmas posições que se veem no CORE.

No CORE, o número de cada máquina é o mesmo que o bootstrapper dá ao nó que lá corre
(configs/cenarioN.json), por isso o número do nó também se lê daqui.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent


@dataclass
class Maquina:
    id: str
    nome: str
    tipo: str          # "router" ou "PC"
    x: float
    y: float
    ips: list[str] = field(default_factory=list)


@dataclass
class Topologia:
    maquinas: dict[str, Maquina]               # nome -> máquina
    ligacoes: list[tuple[str, str]]            # pares de nomes (rede real)

    def nome_do_id(self, ident: str) -> str | None:
        for m in self.maquinas.values():
            if m.id == str(ident):
                return m.nome
        return None


def ler(cenario: str | int) -> Topologia | None:
    """A topologia do cenário, ou None se o ficheiro não existir ou não se conseguir ler."""
    ficheiro = PROJ / "topologias" / f"cenario{cenario}.xml"
    try:
        raiz = ET.parse(ficheiro).getroot()
    except (OSError, ET.ParseError):
        return None
    por_id: dict[str, Maquina] = {}
    for d in raiz.iter("device"):
        pos = d.find("position")
        x, y = (float(pos.get("x", 0)), float(pos.get("y", 0))) if pos is not None else (0.0, 0.0)
        por_id[d.get("id")] = Maquina(d.get("id"), d.get("name"), d.get("type", ""), x, y)
    ligacoes = []
    for ligacao in raiz.iter("link"):
        a, b = por_id.get(ligacao.get("node1")), por_id.get(ligacao.get("node2"))
        if a is None or b is None:
            continue
        ligacoes.append((a.nome, b.nome))
        for maquina, tag in ((a, "iface1"), (b, "iface2")):
            iface = ligacao.find(tag)
            if iface is not None and iface.get("ip4"):
                maquina.ips.append(iface.get("ip4"))
    return Topologia({m.nome: m for m in por_id.values()}, ligacoes)
