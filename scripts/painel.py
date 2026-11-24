#!/usr/bin/env python3
"""Painel do cenário: lê os logs dos nós (logs/cenarioN-*.log) e mostra, de 2 em 2 s, por onde
passa cada vídeo (a árvore), o que cada cliente está a ver, o estado de cada nó e os últimos
acontecimentos, sempre com os nomes dos nós.

    python3 scripts/painel.py                  # cenário que está a correr; atualiza sozinho (Ctrl+C sai)
    python3 scripts/painel.py 3                # cenário 3
    python3 scripts/painel.py 3 --uma-vez      # mostra uma vez e sai (bom para prints)
    python3 scripts/painel.py 3 --hora 16:05:30   # como estava a rede a essa hora
    python3 scripts/painel.py 3 --sem-cor      # sem cores (ex: para guardar num ficheiro)

Só lê os ficheiros de log: não fala com os nós e não precisa de sudo. É uma ferramenta de
teste, que corre fora do overlay: nenhum nó fica a conhecer a árvore por causa dela.
"""
from __future__ import annotations

import re
import shutil
import sys
import time

import logs_ott as L

ATUALIZAR_S = 2
LARGURA_DUAS_COLUNAS = 120  # abaixo disto, as árvores e os clientes ficam um por baixo do outro

ESTILOS = {"negrito": "1", "fraco": "2", "invertido": "7", "vermelho": "31", "verde": "32",
           "amarelo": "33", "azul": "34", "magenta": "35", "ciano": "36", "cinza": "90"}
ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")

SIMBOLO = {L.ATIVO: ("●", "verde"), L.A_ARRANCAR: ("◌", "amarelo"), L.SEM_SINAL: ("✖", "vermelho"),
           L.MORTO: ("✖", "vermelho"), L.ERRO: ("✖", "vermelho"), L.PARADO: ("■", "cinza"),
           L.TERMINOU: ("■", "cinza")}
TIPO = {L.FALHA: ("✖", "vermelho"), L.RECUPERA: ("↻", "verde"), L.ACAO: ("»", "ciano"),
        L.VIDEO: ("▶", "magenta"), L.ERRO_EV: ("!", "vermelho")}


class Tinta:
    """Põe cores ANSI no texto (ou não, se a saída não for um terminal ou com --sem-cor)."""
    def __init__(self, ativa: bool):
        self.ativa = ativa

    def __call__(self, texto: str, *estilos: str) -> str:
        if not self.ativa or not estilos:
            return texto
        return f"\033[{';'.join(ESTILOS[e] for e in estilos)}m{texto}\033[0m"


def visivel(texto: str) -> int:
    return len(ANSI.sub("", texto))


def ajustar(texto: str, largura: int) -> str:
    """Corta ou completa com espaços até `largura` caracteres visíveis (sem estragar as cores)."""
    saida, n, i = [], 0, 0
    while i < len(texto) and n < largura:
        m = ANSI.match(texto, i)
        if m:
            saida.append(m.group())
            i = m.end()
            continue
        if n == largura - 1 and visivel(texto[i:]) > 1:
            saida.append("…")
            n += 1
            break
        saida.append(texto[i])
        n += 1
        i += 1
    resto = "".join(saida)
    if "\033[" in resto:
        resto += "\033[0m"
    return resto + " " * (largura - n)


def tabela(cabecalho: list[str], linhas: list[list[str]], tinta: Tinta) -> list[str]:
    larguras = [max(visivel(str(x)) for x in col) for col in zip(cabecalho, *linhas)] if linhas else \
        [visivel(c) for c in cabecalho]
    def fmt(lin):
        return "  ".join(ajustar(str(x), w) for x, w in zip(lin, larguras)).rstrip()
    return [tinta(fmt(cabecalho), "fraco")] + [fmt(lin) for lin in linhas]


def titulo(texto: str, largura: int, tinta: Tinta) -> list[str]:
    return [tinta(texto, "negrito", "azul") + " " + tinta("─" * max(0, largura - len(texto) - 1), "azul")]


def pintor(estado: L.Estado, tinta: Tinta):
    """Cor de cada nó na árvore: servidores a verde, clientes a ciano, em baixo a vermelho."""
    def pintar(nome: str, texto: str) -> str:
        no = estado.nos.get(nome)
        if no is not None and no.em_baixo:
            return tinta(texto, "vermelho", "negrito")
        if nome.startswith("S"):
            return tinta(texto, "verde", "negrito")
        if nome.startswith("C"):
            return tinta(texto, "ciano")
        return texto
    return pintar


def bloco_arvores(estado: L.Estado, largura: int, tinta: Tinta) -> list[str]:
    linhas = titulo("POR ONDE PASSA O VÍDEO", largura, tinta)
    arvs = L.arvores(estado)
    if not arvs:
        linhas.append(tinta("  ainda nenhum vídeo a passar", "fraco"))
    for arv in arvs:
        linhas += ["  " + x for x in L.texto_arvore(arv, estado, pintor(estado, tinta))]
    return linhas


def bloco_clientes(estado: L.Estado, largura: int, tinta: Tinta) -> list[str]:
    linhas = titulo("CLIENTES", largura, tinta)
    clientes = sorted((n for n in estado.nos.values() if n.cliente), key=lambda n: L.ordem(n.nome))
    if not clientes:
        return linhas + [tinta("  ainda nenhum cliente", "fraco")]
    nm = estado.nome
    corpo = []
    for n in clientes:
        if n.em_baixo:
            video = tinta(f"✖ {n.estado}", "vermelho")
        elif n.videos:
            video = tinta("▶ ", "magenta") + "; ".join(f"vídeo {v} de {nm(s)}" for v, (s, _c) in sorted(n.videos.items()))
        else:
            video = tinta("à espera", "amarelo")
        caminho, atraso = "-", "-"
        if n.ping:
            _hora, saltos, _s, _v = n.ping
            caminho = " → ".join(nm(i) for i, _ms in saltos)
            total = sum(ms for _i, ms in saltos if ms is not None)
            atraso = f"{total:.1f} ms".replace(".", ",")
        perda, paragem = "-", "-"
        if n.player and n.player.esperados:
            pct = n.player.falta_pct
            cor = "verde" if pct < 1 else "amarelo" if pct < 5 else "vermelho"
            perda = tinta(f"{pct:.1f} %".replace(".", ","), cor)
        if n.player and n.player.paragem:
            hora, ms = n.player.paragem
            paragem = f"{ms} ms às {hora[:8]}"
        corpo.append([tinta(n.nome, "ciano", "negrito"), video, caminho, atraso, perda, paragem])
    linhas += ["  " + x for x in tabela(["Cliente", "A ver", "Caminho (último PING)", "Atraso",
                                         "Frames em falta", "Última paragem"], corpo, tinta)]
    return linhas


def bloco_nos(estado: L.Estado, largura: int, tinta: Tinta) -> list[str]:
    linhas = titulo("NÓS", largura, tinta)
    nm = estado.nome
    servidores = estado.servidores()
    corpo = []
    for n in sorted((n for n in estado.nos.values() if n.id or n.pais), key=lambda n: L.ordem(n.nome)):
        simbolo, cor = SIMBOLO.get(n.estado, ("?", "amarelo"))
        pais = []
        for srv in servidores:
            if srv == n.id:
                pais.append(tinta("(é este)", "verde"))
            elif srv in n.pais:
                pai = n.pais[srv][0]
                pais.append(nm(pai) if pai else tinta("nenhum", "vermelho"))
            else:
                pais.append(tinta("?", "fraco"))
        vizinhos = " ".join(sorted((nm(v) for v in n.vizinhos), key=L.ordem)) or tinta("nenhum", "fraco")
        if n.em_baixo:  # o que o log diz dele já é velho
            pais, vizinhos = [tinta(ANSI.sub("", p), "fraco") for p in pais], tinta(ANSI.sub("", vizinhos), "fraco")
        corpo.append([tinta(f"{simbolo} {n.nome}", cor, "negrito"), n.id or "?", tinta(n.estado, cor),
                      *pais, vizinhos])
    linhas += ["  " + x for x in tabela(["Nó", "ID", "Estado", *[f"Pai p/ {nm(s)}" for s in servidores],
                                         "Vizinhos ativos"], corpo, tinta)]
    return linhas


def bloco_acontecimentos(estado: L.Estado, largura: int, n: int, tinta: Tinta) -> list[str]:
    linhas = titulo("ÚLTIMOS ACONTECIMENTOS", largura, tinta)
    evs = estado.acontecimentos[-n:] if n > 0 else []
    if not evs:
        linhas.append(tinta("  nada ainda", "fraco"))
    for ev in evs:
        simbolo, cor = TIPO.get(ev.tipo, ("·", "cinza"))
        linhas.append(f"  {tinta(ev.hora, 'fraco')}  {tinta(simbolo, cor, 'negrito')} {tinta(ev.texto, cor) if ev.tipo in (L.FALHA, L.ERRO_EV) else ev.texto}")
    return linhas


def cabecalho(estado: L.Estado, largura: int, tinta: Tinta, ao_vivo: bool) -> list[str]:
    nos = [n for n in estado.nos.values() if n.id or n.pais]
    ativos = sum(n.estado == L.ATIVO for n in nos)
    baixo = sum(n.em_baixo for n in nos)
    arrancar = sum(n.estado == L.A_ARRANCAR for n in nos)
    clientes = [n for n in estado.nos.values() if n.cliente]
    a_ver = sum(bool(n.videos) and not n.em_baixo for n in clientes)
    esq = f" OTT · cenário {estado.cenario} "
    quando = time.strftime("%H:%M:%S") if ao_vivo else f"estado às {estado.hora[:8] or '?'}"
    dir_ = (f" {quando} · {len(nos)} nós: {ativos} ativos"
            + (f", {arrancar} a arrancar" if arrancar else "")
            + (f", {baixo} em baixo" if baixo else "")
            + f" · {a_ver} de {len(clientes)} clientes a ver vídeo ")
    meio = " " * max(1, largura - len(esq) - len(dir_))
    # A barra fica vermelha enquanto houver um nó em baixo, para se ver de longe.
    return [tinta(esq + meio + dir_, "invertido", "negrito", "vermelho" if baixo else "azul"), ""]


def desenhar(cenario: str, ate: str | None, tinta: Tinta, largura: int, altura: int, ao_vivo: bool) -> list[str]:
    estado = L.analisar(cenario, ate)
    out = cabecalho(estado, largura, tinta, ao_vivo)
    if largura >= LARGURA_DUAS_COLUNAS:
        esq_l = max(34, min(48, largura * 2 // 7))
        dir_l = largura - esq_l - 3
        esq = bloco_arvores(estado, esq_l, tinta)
        dir_ = bloco_clientes(estado, dir_l, tinta)
        for i in range(max(len(esq), len(dir_))):
            a = esq[i] if i < len(esq) else ""
            b = dir_[i] if i < len(dir_) else ""
            out.append(ajustar(a, esq_l) + tinta(" │ ", "azul") + b)
    else:
        out += bloco_arvores(estado, largura, tinta) + [""] + bloco_clientes(estado, largura, tinta)
    out.append("")
    out += bloco_nos(estado, largura, tinta)
    out.append("")
    legenda = tinta("  ● ativo  ◌ a arrancar  ✖ em baixo  ■ parado   ▶ a ver vídeo   "
                    + ("Ctrl+C para sair" if ao_vivo else ""), "fraco")
    sobra = altura - len(out) - 3 if ao_vivo else 12
    out += bloco_acontecimentos(estado, largura, max(3, sobra), tinta)
    out += ["", legenda]
    return [ajustar(x, largura).rstrip() for x in out]


def cenario_atual() -> str | None:
    try:
        return (L.LOGS / ".cenario_atual").read_text().strip()
    except OSError:
        return None


def main() -> None:
    args = sys.argv[1:]
    cenario = next((a for a in args if a in ("1", "2", "3")), None) or cenario_atual()
    if cenario is None:
        sys.exit("Uso: python3 scripts/painel.py [1|2|3] [--uma-vez] [--hora HH:MM:SS] [--sem-cor]")
    ate = None
    if "--hora" in args:
        i = args.index("--hora")
        if i + 1 >= len(args) or not re.fullmatch(r"\d\d:\d\d:\d\d(\.\d{1,3})?", args[i + 1]):
            sys.exit("--hora: no formato HH:MM:SS (ex: 16:05:30)")
        ate = args[i + 1]
    tinta = Tinta(sys.stdout.isatty() and "--sem-cor" not in args)
    tamanho = shutil.get_terminal_size((150, 50))
    if "--uma-vez" in args or ate:
        print("\n".join(desenhar(cenario, ate, tinta, tamanho.columns, tamanho.lines, False)))
        return
    sys.stdout.write("\033[?1049h\033[?25l")  # ecrã à parte e sem cursor, para não piscar
    try:
        while True:
            tamanho = shutil.get_terminal_size((150, 50))
            linhas = desenhar(cenario, None, tinta, tamanho.columns, tamanho.lines, True)
            sys.stdout.write("\033[H" + "\n".join(x + "\033[K" for x in linhas[:tamanho.lines]) + "\033[J")
            sys.stdout.flush()
            time.sleep(ATUALIZAR_S)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
