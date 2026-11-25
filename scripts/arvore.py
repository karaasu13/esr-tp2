#!/usr/bin/env python3
"""Desenha a árvore de cada vídeo (por onde passa, de quem para quem) a partir dos logs dos nós,
em texto no terminal e numa figura SVG com as posições do CORE, para o relatório e a defesa.

    python3 scripts/arvore.py                    # cenário que está a correr, estado de agora
    python3 scripts/arvore.py 3 --hora 16:05:30  # como estava a essa hora (ex: antes de uma falha)
    python3 scripts/arvore.py 3 --saida relatorio/figuras/arvore.svg
    python3 scripts/arvore.py 3 --seguir         # refaz a figura de 2 em 2 s (abrir o .html no browser)
    python3 scripts/arvore.py 3 --rede-real      # com os routers e os cabos da rede real, a cinzento
    python3 scripts/arvore.py 3 --com-clientes   # também os clientes (por omissão ficam de fora)

A figura usa as posições do CORE (topologias/cenarioN.xml) e mostra os vizinhos do overlay a
tracejado e, por cima, a árvore de cada vídeo com setas de cor. Os clientes ficam de fora, como
pediu o professor: a árvore é a do overlay, do servidor até aos nós que replicam o vídeo. Por
omissão vai para logs/arvore-cenarioN.svg (e logs/arvore-cenarioN.html, que se atualiza sozinho).

Tal como o painel, só lê os logs: é uma ferramenta de teste que corre fora do overlay, e nenhum
nó fica a conhecer a árvore por causa dela.
"""
from __future__ import annotations

import math
import re
import sys
import time
from html import escape
from pathlib import Path

import logs_ott as L

# As cores dos vídeos não se confundem com as dos nós (servidor verde, cliente azul).
CORES_FLUXO = ["#e8590c", "#7048e8", "#d6336c", "#f59f00", "#0ca678", "#845ef7"]
COR_SERVIDOR, COR_NO, COR_CLIENTE, COR_BAIXO = "#2b8a3e", "#495057", "#1971c2", "#e03131"
RAIO = 19
MARGEM = 70
LARGURA_LEGENDA = 230


def cenario_atual() -> str | None:
    return L.cenario_atual()  # o do script, ou o dos nós arrancados à mão (logs/core-*/)


def posicoes(estado: L.Estado) -> dict[str, tuple[float, float]]:
    """Posição de cada máquina: a do CORE; quem não estiver na topologia vai para uma fila em baixo."""
    pos = {}
    if estado.topologia:
        pos = {m.nome: (m.x, m.y) for m in estado.topologia.maquinas.values()}
    faltam = sorted((n for n in estado.nos if n not in pos), key=L.ordem)
    if faltam:
        y = max((p[1] for p in pos.values()), default=0) + 120
        for i, nome in enumerate(faltam):
            pos[nome] = (100 + 110 * i, y)
    return pos


def cor_no(nome: str, no: L.No | None) -> str:
    if no is not None and no.em_baixo:
        return COR_BAIXO
    return COR_SERVIDOR if nome.startswith("S") else COR_CLIENTE if nome.startswith("C") else COR_NO


def svg(estado: L.Estado, rede_real: bool = False, com_clientes: bool = False) -> str:
    pos = posicoes(estado)
    overlay = set(estado.nos) | {estado.nome(i) for n in estado.nos.values() for i in n.vizinhos}
    if not com_clientes:
        overlay -= L.clientes(estado) | {n for n in overlay if n.startswith("C") and n not in estado.nos}
    usados = [p for nome, p in pos.items() if rede_real or nome in overlay]
    if not usados:
        usados = [(0.0, 0.0)]
    x0, y0 = min(p[0] for p in usados) - MARGEM, min(p[1] for p in usados) - MARGEM - 40
    x1, y1 = max(p[0] for p in usados) + MARGEM, max(p[1] for p in usados) + MARGEM
    largura, altura = (x1 - x0) + LARGURA_LEGENDA, max(y1 - y0, 360)
    arvs = L.arvores(estado, com_clientes)
    cor_de = {(a.servidor, a.video): CORES_FLUXO[i % len(CORES_FLUXO)] for i, a in enumerate(arvs)}

    def xy(nome):
        x, y = pos[nome]
        return x - x0, y - y0

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura:.0f} {altura:.0f}" '
           f'width="{largura:.0f}" height="{altura:.0f}" font-family="Helvetica, Arial, sans-serif">',
           "<defs>"]
    for (srv, video), cor in cor_de.items():
        out.append(f'<marker id="seta-{srv}-{video}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" '
                   f'markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{cor}"/></marker>')
    out.append("</defs>")
    out.append('<rect width="100%" height="100%" fill="white"/>')
    quando = estado.hora[:8] if estado.hora else time.strftime("%H:%M:%S")
    out.append(f'<text x="20" y="32" font-size="20" font-weight="bold" fill="#1f2328">'
               f'Cenário {estado.cenario} · árvore dos vídeos às {quando}</text>')

    # Rede real: cabos e routers, a cinzento claro
    if rede_real and estado.topologia:
        for a, b in estado.topologia.ligacoes:
            if a in pos and b in pos:
                (ax, ay), (bx, by) = xy(a), xy(b)
                out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="#e9ecef" stroke-width="2"/>')
        for m in estado.topologia.maquinas.values():
            if m.tipo == "router" and m.nome in pos:
                x, y = xy(m.nome)
                out.append(f'<rect x="{x - 13:.1f}" y="{y - 13:.1f}" width="26" height="26" rx="5" fill="#f8f9fa" stroke="#dee2e6"/>'
                           f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="10" text-anchor="middle" fill="#adb5bd">{escape(m.nome)}</text>')

    # Vizinhos do overlay, a tracejado
    pares = set()
    for no in estado.nos.values():
        if no.em_baixo:
            continue
        for v in no.vizinhos:
            outro = estado.nome(v)
            if outro in pos and no.nome in pos and outro in overlay and no.nome in overlay:
                pares.add(tuple(sorted((no.nome, outro))))
    for a, b in sorted(pares):
        (ax, ay), (bx, by) = xy(a), xy(b)
        out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="#adb5bd" '
                   f'stroke-width="1.2" stroke-dasharray="5 4"/>')

    # Árvores: uma seta de cor por cada "envia a", afastadas se várias passarem na mesma ligação
    arestas = {}
    for arv in arvs:
        for pai, filhos in arv.filhos.items():
            for f in filhos:
                arestas.setdefault(tuple(sorted((pai, f))), []).append((arv, pai, f))
    for lista in arestas.values():
        for k, (arv, pai, f) in enumerate(lista):
            if pai not in pos or f not in pos:
                continue
            (ax, ay), (bx, by) = xy(pai), xy(f)
            d = math.hypot(bx - ax, by - ay) or 1
            ux, uy = (bx - ax) / d, (by - ay) / d
            desvio = (k - (len(lista) - 1) / 2) * 6
            nx, ny = -uy * desvio, ux * desvio
            sx, sy = ax + ux * (RAIO + 2) + nx, ay + uy * (RAIO + 2) + ny
            ex, ey = bx - ux * (RAIO + 4) + nx, by - uy * (RAIO + 4) + ny
            cor = cor_de[(arv.servidor, arv.video)]
            out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="{cor}" '
                       f'stroke-width="4" stroke-linecap="round" marker-end="url(#seta-{arv.servidor}-{arv.video})"/>')

    # Nós do overlay
    a_ver = {}
    for arv in arvs:
        for nome in arv.ao_player:
            a_ver.setdefault(nome, []).append(cor_de[(arv.servidor, arv.video)])
    for nome in sorted(overlay, key=L.ordem):
        if nome not in pos:
            continue
        no = estado.nos.get(nome)
        x, y = xy(nome)
        cor = cor_no(nome, no)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{RAIO}" fill="{cor}" stroke="white" stroke-width="2"/>'
                   f'<text x="{x:.1f}" y="{y + 5:.1f}" font-size="14" font-weight="bold" text-anchor="middle" '
                   f'fill="white">{escape(nome)}</text>')
        rotulo = f"nó {no.id}" if no and no.id else ""
        if no is not None and no.em_baixo:
            rotulo = f"✖ {no.estado}"
        if rotulo:
            out.append(f'<text x="{x:.1f}" y="{y + RAIO + 14:.1f}" font-size="11" text-anchor="middle" '
                       f'fill="{COR_BAIXO if no and no.em_baixo else "#57606a"}">{escape(rotulo)}</text>')
        for k, c in enumerate(a_ver.get(nome, [])):  # ▶: este cliente está a ver um vídeo
            bx, by = x + RAIO - 2 + 15 * k, y - RAIO + 2
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="8" fill="{c}" stroke="white" stroke-width="1.5"/>'
                       f'<path d="M{bx - 2.5:.1f},{by - 4:.1f} L{bx + 4:.1f},{by:.1f} L{bx - 2.5:.1f},{by + 4:.1f} z" fill="white"/>')

    # Legenda
    lx, ly = largura - LARGURA_LEGENDA + 10, 70
    out.append(f'<text x="{lx}" y="{ly}" font-size="14" font-weight="bold" fill="#1f2328">Legenda</text>')
    ly += 24
    for arv in arvs:
        cor = cor_de[(arv.servidor, arv.video)]
        out.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 34}" y2="{ly - 4}" stroke="{cor}" stroke-width="4" '
                   f'marker-end="url(#seta-{arv.servidor}-{arv.video})"/>'
                   f'<text x="{lx + 44}" y="{ly}" font-size="12" fill="#1f2328">{escape(arv.raiz)} · vídeo {arv.video}</text>')
        ly += 20
    if not arvs:
        out.append(f'<text x="{lx}" y="{ly}" font-size="12" fill="#57606a">nenhum vídeo a passar</text>')
        ly += 20
    itens = [("dash", "vizinhos no overlay"), ("viz", "a ver vídeo (cor do vídeo)"),
             ("S", "servidor"), ("N", "nó intermédio"), ("C", "cliente"), ("X", "em baixo")]
    if not com_clientes:
        itens = [i for i in itens if i[0] not in ("viz", "C")]
    if rede_real:
        itens.append(("R", "rede real (routers e cabos)"))
    for tipo, texto in itens:
        if tipo == "dash":
            out.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 34}" y2="{ly - 4}" stroke="#adb5bd" stroke-dasharray="5 4"/>')
        elif tipo == "viz":
            out.append(f'<circle cx="{lx + 17}" cy="{ly - 4}" r="7" fill="{CORES_FLUXO[0]}"/>')
        elif tipo == "R":
            out.append(f'<rect x="{lx + 8}" y="{ly - 13}" width="18" height="18" rx="4" fill="#f6f8fa" stroke="#d0d7de"/>')
        else:
            cor = {"S": COR_SERVIDOR, "N": COR_NO, "C": COR_CLIENTE, "X": COR_BAIXO}[tipo]
            out.append(f'<circle cx="{lx + 17}" cy="{ly - 4}" r="8" fill="{cor}"/>')
        out.append(f'<text x="{lx + 44}" y="{ly}" font-size="12" fill="#1f2328">{escape(texto)}</text>')
        ly += 20
    out.append("</svg>")
    return "\n".join(out)


def html(svg_texto: str, cenario: str) -> str:
    return (f'<!DOCTYPE html><html lang="pt"><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">'
            f'<title>Árvore · cenário {cenario}</title></head>'
            f'<body style="margin:0;background:#f6f8fa">{svg_texto}</body></html>')


def escrever(estado: L.Estado, saida: Path, rede_real: bool, com_clientes: bool = False) -> Path:
    figura = svg(estado, rede_real, com_clientes)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(figura, encoding="utf-8")
    pagina = saida.with_suffix(".html")
    pagina.write_text(html(figura, estado.cenario), encoding="utf-8")
    return pagina


def texto(estado: L.Estado, com_clientes: bool = False) -> str:
    arvs = L.arvores(estado, com_clientes)
    if not arvs:
        return "Ainda nenhum vídeo a passar."
    linhas = []
    for arv in arvs:
        linhas += L.texto_arvore(arv, estado) + [""]
    return "\n".join(linhas).rstrip()


def main() -> None:
    args = sys.argv[1:]
    cenario = next((a for a in args if a in ("1", "2", "3")), None) or cenario_atual()
    if cenario is None:
        sys.exit("Uso: python3 scripts/arvore.py [1|2|3] [--hora HH:MM:SS] [--saida F.svg] [--seguir] [--rede-real]")

    def valor(opcao: str) -> str | None:
        if opcao not in args:
            return None
        i = args.index(opcao)
        if i + 1 >= len(args):
            sys.exit(f"{opcao}: falta o valor")
        return args[i + 1]

    ate = valor("--hora")
    if ate and not re.fullmatch(r"\d\d:\d\d:\d\d(\.\d{1,3})?", ate):
        sys.exit("--hora: no formato HH:MM:SS (ex: 16:05:30)")
    saida = Path(valor("--saida") or L.LOGS / f"arvore-cenario{cenario}.svg")
    rede_real = "--rede-real" in args
    com_clientes = "--com-clientes" in args

    estado = L.analisar(cenario, ate)
    print(f"Cenário {cenario} · estado às {estado.hora[:8] or '?'}\n")
    print(texto(estado, com_clientes))
    pagina = escrever(estado, saida, rede_real, com_clientes)
    print(f"\nFigura: {saida}  (para ver a atualizar: abre {pagina} no browser)")
    if "--seguir" not in args:
        return
    if ate:
        sys.exit("--seguir e --hora não fazem sentido juntos")
    print("A refazer de 2 em 2 s (Ctrl+C para parar)...")
    try:
        while True:
            time.sleep(2)
            escrever(L.analisar(cenario), saida, rede_real, com_clientes)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
