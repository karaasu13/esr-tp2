#!/usr/bin/env python3
"""Árvore de distribuição de cada vídeo, desenhada a partir dos logs dos nós, sem os clientes.

    python3 scripts/arvore.py                    # sessão à mão mais recente (logs/core-*/)
    python3 scripts/arvore.py 3                  # cenário 3 arrancado pelo script
    python3 scripts/arvore.py --pasta DIR        # os logs de uma pasta
    ... --png logs/arvore.png                    # também uma figura (precisa do Graphviz: dot)

Para cada fluxo (servidor, vídeo) ativo, segue a tabela de fluxos de cada nó (a última linha
`fluxo (S, v): pai=… destinos=…` do log) desde o servidor. Os clientes ficam de fora: a árvore é a
do overlay, entre o servidor e os nós que replicam o vídeo.

Como o painel, é uma ferramenta de teste que corre fora do overlay: junta os logs de todos os nós
depois de os ler. Nenhum nó conhece a árvore.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import painel  # noqa: E402  (mesma pasta: reaproveita a leitura dos logs)

CORES = ["#C55A11", "#2E75B6", "#548235", "#7030A0", "#BF9000", "#C00000"]


def e_cliente(n: "painel.No") -> bool:
    """Pediu um vídeo (--ver), tem player, ou recebe um fluxo ativo só para si (sem destinos)."""
    return n.cliente or any(ativo and not destinos and srv != n.id
                            for (srv, _v), (destinos, ativo) in n.fluxos.items())


def arvores(fonte: painel.Fonte):
    """{(servidor, vídeo): {nó: [filhos]}} só com nós que não são clientes, e os nós lidos."""
    nos, nm, _ = painel.analisar(fonte)
    por_id = {n.id: n for n in nos.values() if n.id}

    def cliente(i: str) -> bool:
        n = por_id.get(i)
        return e_cliente(n) if n else nm(i).startswith("C")

    resultado = {}
    fluxos = sorted({f for n in nos.values() for f, (_d, ativo) in n.fluxos.items() if ativo})
    for srv, video in fluxos:
        filhos = {}
        for n in nos.values():
            destinos, ativo = n.fluxos.get((srv, video), ([], False))
            if not ativo or not n.id or cliente(n.id) or n.estado != "ativo":
                continue  # inativo, cliente, ou o nó já não está a correr (parado, morto, sem sinal)
            filhos[n.id] = [d for d in destinos if not cliente(d)]
        resultado[(srv, video)] = filhos
    return resultado, nos, nm


def texto(fonte: painel.Fonte) -> str:
    resultado, _nos, nm = arvores(fonte)
    if not resultado:
        return "Nenhum fluxo ativo nos logs."
    linhas = []
    for (srv, video), filhos in resultado.items():
        linhas.append(f"Vídeo {video} de {nm(srv)} (sem os clientes)")
        vistos = set()

        def ramo(no: str, prefixo: str, ultimo: bool, raiz: bool = False) -> None:
            marca = "" if raiz else ("└── " if ultimo else "├── ")
            repetido = no in vistos
            linhas.append(f"{prefixo}{marca}{nm(no)}{'  (repetido: ciclo?)' if repetido else ''}")
            if repetido:
                return
            vistos.add(no)
            seguintes = sorted(filhos.get(no, []), key=lambda i: painel.ordem(nm(i)))
            novo = prefixo + ("" if raiz else ("    " if ultimo else "│   "))
            for k, filho in enumerate(seguintes):
                ramo(filho, novo, k == len(seguintes) - 1)

        ramo(srv, "", True, raiz=True)
        soltos = sorted(set(filhos) - vistos, key=lambda i: painel.ordem(nm(i)))
        if soltos:  # nós com o fluxo ativo que não estão pendurados no servidor (logs a meio)
            linhas.append(f"  (sem ligação ao {nm(srv)} nos logs: {', '.join(nm(i) for i in soltos)})")
        linhas.append("")
    return "\n".join(linhas).rstrip()


def dot(fonte: painel.Fonte) -> str:
    """O overlay (sem clientes) a cinzento e, por cima, uma árvore de cor por fluxo."""
    resultado, nos, nm = arvores(fonte)
    sem_clientes = {n.id: n for n in nos.values()
                    if n.id and not e_cliente(n) and n.estado == "ativo"}
    out = ["graph arvore {", '  graph [bgcolor=white, pad=0.2, fontname="DejaVu Sans"];',
           '  node [fontname="DejaVu Sans", fontsize=12, style=filled, penwidth=1.2];',
           '  edge [fontname="DejaVu Sans", fontsize=10];']
    for i, n in sorted(sem_clientes.items(), key=lambda x: painel.ordem(x[1].nome)):
        servidor = any(srv == i for srv, _v in resultado)
        forma = 'shape=box, fillcolor="#F4B183"' if servidor else 'shape=ellipse, fillcolor="#BDD7EE"'
        out.append(f'  "{n.nome}" [{forma}];')
    # Uma ligação do overlay que já é ramo de uma árvore não se desenha a cinzento por baixo.
    feitas = {frozenset((pai, filho)) for filhos in resultado.values()
              for pai, lista in filhos.items() for filho in lista}
    for i, n in sem_clientes.items():
        for v in n.vizinhos:
            if v in sem_clientes and frozenset((i, v)) not in feitas:
                feitas.add(frozenset((i, v)))
                out.append(f'  "{n.nome}" -- "{nm(v)}" [color="#BFBFBF", style=dashed];')
    for k, ((srv, video), filhos) in enumerate(resultado.items()):
        cor = CORES[k % len(CORES)]
        for pai, lista in filhos.items():
            for filho in lista:
                out.append(f'  "{nm(pai)}" -- "{nm(filho)}" [color="{cor}", penwidth=2.4, dir=forward, '
                           f'label=" {nm(srv)}/{video}", fontcolor="{cor}"];')
    out.append("}")
    return "\n".join(out)


def main() -> None:
    args = sys.argv[1:]
    png = None
    if "--png" in args:
        i = args.index("--png")
        if i + 1 >= len(args):
            sys.exit("--png precisa do nome do ficheiro")
        png = Path(args[i + 1])
        del args[i:i + 2]
    if args and args[0] in ("1", "2", "3") and len(args) == 1:
        fonte = painel.Fonte(cenario=args[0])
    elif len(args) == 2 and args[0] == "--pasta":
        fonte = painel.Fonte(pasta=Path(args[1]).resolve())
    elif not args:
        pasta = painel.sessao_mais_recente()
        if pasta is None:
            sys.exit("Não há logs de nós arrancados à mão em logs/core-*/. "
                     "Para os do script: python3 scripts/arvore.py 2|3")
        fonte = painel.Fonte(pasta=pasta)
    else:
        sys.exit("Uso: python3 scripts/arvore.py [2|3 | --pasta DIR] [--png FICHEIRO]")
    print(texto(fonte))
    if png is not None:
        fonte_dot = png.with_suffix(".dot")
        fonte_dot.write_text(dot(fonte), encoding="utf-8")
        if shutil.which("dot") is None:
            sys.exit(f"Escrevi {fonte_dot}, mas não encontrei o Graphviz (dot) para fazer o PNG.")
        subprocess.run(["dot", "-Tpng", "-Gdpi=150", str(fonte_dot), "-o", str(png)], check=True)
        print(f"\nFigura em {png} (fonte em {fonte_dot})")


if __name__ == "__main__":
    main()
