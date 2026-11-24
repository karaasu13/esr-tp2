#!/usr/bin/env python3
"""Painel do cenário: lê os logs dos nós e mostra, de 2 em 2 s, o estado de cada nó, o caminho de
cada cliente e os últimos acontecimentos, sempre com os nomes dos nós.

    python3 scripts/painel.py              # nós arrancados à mão: a sessão do CORE mais recente
                                           # (logs/core-<sessão>/, escritos pelos próprios nós)
    python3 scripts/painel.py 3            # cenário 3 arrancado pelo script (logs/cenario3-*.log)
    python3 scripts/painel.py --pasta DIR  # os logs de uma pasta (<nó>.log, <nó>-player.log)
    ... --uma-vez                          # mostra uma vez e sai

Corre no PC, fora do CORE, e pode abrir-se a qualquer altura: relê os logs desde o início.

Só lê os ficheiros de log: não fala com os nós e não precisa de sudo. É uma ferramenta de
teste, que corre fora do overlay: nenhum nó fica a conhecer a árvore por causa dela.
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "logs"
SUFIXOS = {"player", "bootstrapper", "medidor", "debito"}  # e "video", "video0", "video1", ...
ATUALIZAR_S = 2
N_ACONTECIMENTOS = 16
JUNTAR_S = 2.0  # avisos iguais de vizinhos diferentes, com menos do que isto, ficam numa linha

LINHA = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3}) (?:(?!(?:INFO|WARNING|ERROR) )\S+ )?(INFO|WARNING|ERROR) (.*)$")
R_ID = re.compile(r"^\[(\d+)\] ")
R_PAI = re.compile(r"^\[\d+\] pai para o servidor (\d+): (nenhum|\d+)(?: \(custo ([\d.]+)\))?(?: \((.*)\))?$")
R_VIDEO = re.compile(r"^\[\d+\] vídeo (\d+): servidor (\d+) \(custo ([\d.]+)\)(?:, antes (\d+))?$")
R_FLUXO = re.compile(r"^\[\d+\] fluxo \((\d+), (\d+)\): pai=(\S+)(?: custo=\S+)? destinos=(.*) ativo=(True|False)$")
R_VIZ = re.compile(r"^\[\d+\] tabela de vizinhos: (.*)$")
R_PING = re.compile(r"^PING (\d+) do vídeo \((\d+), (\d+)\): (.*)$")
R_BAIXO = re.compile(r"^\[\d+\] vizinho (\d+) em baixo \((.*)\)$")
R_SAIU = re.compile(r"^\[\d+\] vizinho (\d+) avisou que vai sair$")
R_VOLTOU = re.compile(r"^\[\d+\] vizinho (\d+) (?:voltou|ativo)$")
R_REINICIO = re.compile(r"^\[\d+\] (?:HELLO repetido de (\d+);|vizinho (\d+) reiniciou)")
R_PLAYER_RESUMO = re.compile(r"^player: (?:a sair: )?frame \d+ · recebidos \d+ de \d+ · (em falta .*)$")
R_PLAYER_PARAGEM = re.compile(r"^player: (\d+) ms sem frame novo \((.*)\)$")
R_NOSH = re.compile(r"^no\.sh: (.*)$")
R_AVO = re.compile(r"^\[\d+\] avô guardado \(via (\d+)\): (.*)$")
R_SOCORRO = re.compile(r"^\[\d+\] sem vizinhos vivos: pede socorro ao avô (\d+)")
R_ACEITE = re.compile(r"^\[\d+\] pedido de socorro do nó (\d+) .*: aceite como vizinho$")
R_FIM_EMERG = re.compile(r"^\[\d+\] o vizinho (\d+) voltou: fecha a ligação de emergência ao (\d+)$")


@dataclass
class Registo:
    hora: str
    nivel: str
    texto: str


@dataclass
class No:
    nome: str
    id: str = ""
    estado: str = "a arrancar"
    ultima_hora: str = ""
    pais: dict = field(default_factory=dict)      # servidor -> (pai ou None, custo)
    videos: dict = field(default_factory=dict)    # vídeo -> (servidor, custo)
    fluxos: dict = field(default_factory=dict)    # (servidor, vídeo) -> (destinos, ativo)
    vizinhos: list = field(default_factory=list)
    ping: tuple | None = None                     # (hora, [ids], servidor, vídeo)
    cliente: bool = False
    player: str = ""
    paragem: str = ""


def ordem(nome: str):
    m = re.match(r"([A-Za-z]+)(\d*)", nome)
    letra, num = (m.group(1), int(m.group(2) or 0)) if m else (nome, 0)
    return ({"S": 0, "N": 1, "C": 2}.get(letra[:1], 3), letra, num)


def ler(ficheiro: Path) -> list[Registo]:
    registos, ultima = [], "00:00:00.000"
    try:
        texto = ficheiro.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return registos
    for linha in texto.splitlines():
        m = LINHA.match(linha)
        if m:
            ultima = m.group(1)
            registos.append(Registo(m.group(1), m.group(2), m.group(3)))
        elif linha.startswith("Traceback"):
            registos.append(Registo(ultima, "ERROR", "erro no programa (Traceback no log)"))
    return registos


@dataclass
class Fonte:
    """De onde vêm os logs: os do script (cenario) ou uma pasta de uma sessão à mão (pasta)."""
    cenario: str = ""
    pasta: Path | None = None

    def ficheiros(self):
        if self.pasta is not None:  # logs/core-1/C1-player.log -> ["C1", "player"]
            return [(f, f.stem.split("-")) for f in sorted(self.pasta.glob("*.log"))]
        # logs/cenario3-C1-player.log -> ["C1", "player"]
        return [(f, f.stem.split("-")[1:]) for f in sorted(LOGS.glob(f"cenario{self.cenario}-*.log"))]

    def descricao(self) -> str:
        if self.pasta is not None:
            try:
                return f"Sessão {self.pasta.name} · lido de {self.pasta.relative_to(LOGS.parent)}/"
            except ValueError:
                return f"Lido de {self.pasta}/"
        return f"Cenário {self.cenario} · lido de logs/cenario{self.cenario}-*.log"


def sessao_mais_recente() -> Path | None:
    """A pasta logs/core-<sessão> mais recente (cada nó arrancado à mão escreve lá o seu log)."""
    pastas = [p for p in LOGS.glob("core-*") if p.is_dir()]
    return max(pastas, key=lambda p: p.stat().st_mtime) if pastas else None


def carregar(fonte: Fonte):
    """Lê os logs: {nó: registos do nó overlay} e {nó: registos do player}."""
    principais, players = {}, {}
    for f, partes in fonte.ficheiros():
        if not partes or not partes[0]:
            continue
        nome, sufixo = partes[0], (partes[1] if len(partes) > 1 else "")
        if sufixo == "player":
            players[nome] = ler(f)
        elif sufixo not in SUFIXOS and not sufixo.startswith("video"):
            principais[nome] = ler(f)
    return principais, players


def analisar(fonte: Fonte):
    principais, players = carregar(fonte)
    nos = {nome: No(nome) for nome in principais}
    for nome, regs in principais.items():
        for r in regs:
            m = R_ID.match(r.texto)
            if m and m.group(1) != "0":  # antes do registo no bootstrapper o nó ainda é o 0
                nos[nome].id = m.group(1)
                break
    nomes = {n.id: n.nome for n in nos.values() if n.id}

    def nm(i) -> str:
        return nomes.get(str(i), str(i)) if i not in (None, "None", "nenhum") else "-"

    def traduzir_motivo(motivo: str) -> str:
        return re.sub(r"\b(vizinho|de|que) (\d+)\b", lambda m: f"{m.group(1)} {nm(m.group(2))}", motivo)

    acontecimentos = []     # (hora, texto, chave para juntar, quem viu)
    relatos = {}            # id do nó -> lista de (hora, "baixo"/"cima")
    ativos_para = set()     # (quem relata, sobre quem): já esteve ativo para quem relata
    em_baixo_para = set()   # (quem relata, sobre quem): deu-o como em baixo depois de ativo

    for nome, regs in principais.items():
        no = nos[nome]
        for r in regs:
            t = r.texto
            no.ultima_hora = r.hora
            if no.estado == "a arrancar" and no.id and t.startswith(f"[{no.id}] "):
                no.estado = "ativo"
            if (m := R_PAI.match(t)):
                srv, pai, custo, motivo = m.groups()
                no.pais[srv] = (None if pai == "nenhum" else pai, custo)
                if motivo or pai == "nenhum":
                    texto = f"{nome}: pai para {nm(srv)} → {nm(pai) if pai != 'nenhum' else 'nenhum'}"
                    texto += f" (custo {custo})" if custo else ""
                    texto += f" — {traduzir_motivo(motivo)}" if motivo else ""
                    acontecimentos.append((r.hora, texto, None, None))
            elif (m := R_VIDEO.match(t)):
                video, srv, custo, antes = m.groups()
                no.videos[video] = (srv, custo)
                no.cliente = True
                texto = f"{nome}: vídeo {video} de {nm(srv)} (custo {custo})"
                if antes:
                    texto += f", antes de {nm(antes)}"
                acontecimentos.append((r.hora, texto, None, None))
            elif (m := R_FLUXO.match(t)):
                srv, video, _pai, destinos, ativo = m.groups()
                lista = [] if destinos.startswith("(") else [d.strip() for d in destinos.split(",")]
                no.fluxos[(srv, video)] = (lista, ativo == "True")
            elif (m := R_VIZ.match(t)):
                corpo = m.group(1)
                no.vizinhos = [] if corpo.startswith("(") else [v.split("@")[0] for v in corpo.split(", ")]
            elif (m := R_PING.match(t)):
                _n, srv, video, caminho = m.groups()
                ids = re.findall(r"(?:^|→ )(\d+)", caminho)
                no.ping = (r.hora, ids, srv, video)
            elif (m := R_BAIXO.match(t)):
                outro, motivo = m.groups()
                relatos.setdefault(outro, []).append((r.hora, "baixo"))
                if (no.id, outro) in ativos_para:
                    em_baixo_para.add((no.id, outro))
                    acontecimentos.append((r.hora, f"{nm(outro)} em baixo ({motivo})", ("baixo", outro), nome))
            elif (m := R_SAIU.match(t)):
                outro = m.group(1)
                relatos.setdefault(outro, []).append((r.hora, "baixo"))
                em_baixo_para.add((no.id, outro))
                acontecimentos.append((r.hora, f"{nm(outro)} saiu e avisou", ("saiu", outro), nome))
            elif (m := R_VOLTOU.match(t)):
                outro = m.group(1)
                relatos.setdefault(outro, []).append((r.hora, "cima"))
                if (no.id, outro) in em_baixo_para:
                    em_baixo_para.discard((no.id, outro))
                    acontecimentos.append((r.hora, f"{nm(outro)} voltou", ("voltou", outro), nome))
                ativos_para.add((no.id, outro))
            elif (m := R_REINICIO.match(t)):
                outro = m.group(1) or m.group(2)
                relatos.setdefault(outro, []).append((r.hora, "cima"))
                acontecimentos.append((r.hora, f"{nm(outro)} reiniciou", ("reiniciou", outro), nome))
            elif (m := R_AVO.match(t)):
                via, avos = m.groups()
                avos = re.sub(r"\b(\d+) \([\d.]+\)", lambda x: nm(x.group(1)), avos)
                acontecimentos.append((r.hora, f"{nome}: só tem o vizinho {nm(via)}; guarda o avô: {avos}", None, None))
            elif (m := R_SOCORRO.match(t)):
                acontecimentos.append((r.hora, f"{nome}: sem vizinhos, pede socorro ao avô {nm(m.group(1))}", None, None))
            elif (m := R_ACEITE.match(t)):
                acontecimentos.append((r.hora, f"{nome}: aceita {nm(m.group(1))} como vizinho (socorro)", None, None))
            elif (m := R_FIM_EMERG.match(t)):
                volta, avo = m.groups()
                acontecimentos.append((r.hora, f"{nome}: {nm(volta)} voltou, fecha a ligação de emergência a {nm(avo)}", None, None))
            elif (m := R_NOSH.match(t)):
                acao = m.group(1)
                if "parado" in acao:
                    no.estado = "parado (CLOSE)"
                elif "kill -9" in acao:
                    no.estado = "morto (kill -9)"
                elif "arrancado" in acao:
                    no.estado = "a arrancar"
                acontecimentos.append((r.hora, f"{nome}: {acao}", None, None))
            elif t == "nó OTT terminado":
                if not no.estado.startswith(("parado", "morto")):
                    no.estado = "terminou"
            elif r.nivel == "ERROR":
                no.estado = "erro"
                acontecimentos.append((r.hora, f"{nome}: ERRO — {t}", None, None))

    # Um nó que ainda parece ativo mas que os vizinhos deram como em baixo depois da última
    # linha dele (ex: morto sem aviso, ou ligações cortadas) aparece como "sem sinal".
    for no in nos.values():
        if no.estado == "ativo" and no.id in relatos:
            hora, tipo = max(relatos[no.id])
            if tipo == "baixo" and hora >= no.ultima_hora:
                no.estado = "sem sinal"

    for nome, regs in players.items():
        no = nos.setdefault(nome, No(nome))
        no.cliente = True
        for r in regs:
            if (m := R_PLAYER_RESUMO.match(r.texto)):
                no.player = m.group(1)
            elif (m := R_PLAYER_PARAGEM.match(r.texto)):
                no.paragem = f"{m.group(1)} ms às {r.hora[:8]}"
                acontecimentos.append((r.hora, f"{nome}: vídeo parado {m.group(1)} ms ({m.group(2)})", None, None))

    return nos, nm, juntar(acontecimentos)


def segundos(hora: str) -> float:
    h, m, s = hora.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def juntar(acontecimentos):
    """Junta numa linha o mesmo aviso dado por vizinhos diferentes quase ao mesmo tempo."""
    saida = []  # [hora, texto, chave, [quem viu]]
    for hora, texto, chave, quem in sorted(acontecimentos, key=lambda a: a[0]):
        if chave is not None:
            for a in reversed(saida[-12:]):
                if a[2] == chave and segundos(hora) - segundos(a[0]) <= JUNTAR_S:
                    if quem not in a[3]:
                        a[3].append(quem)
                    break
            else:
                saida.append([hora, texto, chave, [quem]])
        else:
            saida.append([hora, texto, None, []])
    linhas = []
    for hora, texto, chave, quem in saida:
        if quem:
            texto += f" — visto por {', '.join(quem)}"
        linhas.append((hora, texto))
    return linhas


def tabela(cabecalho, linhas) -> list[str]:
    larguras = [max(len(str(x)) for x in col) for col in zip(cabecalho, *linhas)]
    def fmt(lin):
        return "  ".join(str(x).ljust(w) for x, w in zip(lin, larguras)).rstrip()
    return [fmt(cabecalho)] + [fmt(lin) for lin in linhas]


def desenhar(fonte: Fonte) -> str:
    nos, nm, acontecimentos = analisar(fonte)
    largura = shutil.get_terminal_size((150, 50)).columns
    negrito = (lambda s: f"\033[1m{s}\033[0m") if sys.stdout.isatty() else (lambda s: s)
    servidores = sorted({srv for n in nos.values() for srv in n.pais}, key=lambda s: ordem(nm(s)))
    out = [negrito(f"{fonte.descricao()} · {time.strftime('%H:%M:%S')}"
                   f" · atualiza de {ATUALIZAR_S} em {ATUALIZAR_S} s (Ctrl+C para sair)"), ""]

    lista = sorted((n for n in nos.values() if n.id or n.pais), key=lambda n: ordem(n.nome))
    linhas = []
    for n in lista:
        pais = []
        for srv in servidores:
            if srv == n.id:
                pais.append("(é ele)")
            elif srv in n.pais:
                # O custo só aparece no log quando o pai muda, por isso não se mostra: estaria velho.
                pais.append(nm(n.pais[srv][0]))
            else:
                pais.append("?")
        envia = []
        for (srv, video), (destinos, ativo) in sorted(n.fluxos.items()):
            if ativo:
                para = ", ".join(nm(d) for d in destinos) or "player"
                envia.append(f"{nm(srv)}/{video} → {para}")
        linhas.append([n.nome, n.id, n.estado, len(n.vizinhos), *pais, "; ".join(envia) or "-"])
    out.append(negrito("NÓS") + "   (pai = de quem recebe o vídeo desse servidor; viz. = vizinhos ativos; envia = fluxos ativos e para onde)")
    out += tabela(["Nó", "ID", "Estado", "Viz.", *[f"Pai p/ {nm(s)}" for s in servidores], "Envia"], linhas)
    out.append("")

    clientes = sorted((n for n in nos.values() if n.cliente), key=lambda n: ordem(n.nome))
    linhas = []
    for n in clientes:
        if n.videos:
            video = "; ".join(f"{v} de {nm(srv)}" for v, (srv, _c) in sorted(n.videos.items()))
        else:
            video = "; ".join(f"{v} de {nm(srv)}" for (srv, v), (d, a) in n.fluxos.items() if a and not d) or "-"
        caminho = "-"
        if n.ping:
            hora, ids, _srv, _v = n.ping
            caminho = f"{' → '.join(nm(i) for i in ids)}  ({hora[:8]})"
        linhas.append([n.nome, video, caminho, n.player or "-", n.paragem or "-"])
    out.append(negrito("CLIENTES") + "   (caminho = último PING; player = contagem desde que abriu)")
    out += tabela(["Cliente", "Vídeo", "Caminho do vídeo", "Player", "Última paragem"], linhas)
    out.append("")

    out.append(negrito("ÚLTIMOS ACONTECIMENTOS"))
    for hora, texto in acontecimentos[-N_ACONTECIMENTOS:]:
        out.append(f"{hora}  {texto}")
    return "\n".join(linha[:largura] if not linha.startswith("\033") else linha for linha in out)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--uma-vez"]
    if args and args[0] in ("1", "2", "3") and len(args) == 1:
        fonte = Fonte(cenario=args[0])
    elif len(args) == 2 and args[0] == "--pasta":
        fonte = Fonte(pasta=Path(args[1]).resolve())
    elif not args:
        pasta = sessao_mais_recente()
        if pasta is None:
            sys.exit("Não há logs de nós arrancados à mão em logs/core-*/. "
                     "Para os do script: python3 scripts/painel.py 2|3")
        fonte = Fonte(pasta=pasta)
    else:
        sys.exit("Uso: python3 scripts/painel.py [2|3 | --pasta DIR] [--uma-vez]")
    if "--uma-vez" in sys.argv:
        print(desenhar(fonte))
        return
    try:
        while True:
            ecra = desenhar(fonte)
            sys.stdout.write("\033[H\033[2J" + ecra + "\n")
            sys.stdout.flush()
            time.sleep(ATUALIZAR_S)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
