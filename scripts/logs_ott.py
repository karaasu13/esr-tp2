"""Lê os logs de um cenário (logs/cenarioN-*.log) e reconstrói o estado da rede: estado de cada
nó, vizinhos, pais, fluxos, caminho de cada cliente e os acontecimentos, tudo com os nomes dos
nós. É usado pelo painel (scripts/painel.py) e pelo desenho da árvore (scripts/arvore.py).

Só lê ficheiros: não fala com os nós. É uma ferramenta de teste, que corre fora do overlay, por
isso nenhum nó fica a conhecer a árvore por causa dela.

Com `ate="HH:MM:SS"` só conta as linhas até essa hora: dá o estado da rede nesse instante.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import topologia

LOGS = Path(__file__).resolve().parent.parent / "logs"
SUFIXOS = {"player", "bootstrapper", "medidor", "debito"}  # e "video", "video0", "video1", ...
JUNTAR_S = 2.0  # o mesmo aviso dado por vizinhos diferentes com menos do que isto fica numa linha

LINHA = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3}) (?:(?!(?:INFO|WARNING|ERROR) )\S+ )?(INFO|WARNING|ERROR) (.*)$")
R_ID = re.compile(r"^\[(\d+)\] ")
R_PAI = re.compile(r"^\[\d+\] pai para o servidor (\d+): (nenhum|\d+)(?: \(custo ([\d.]+)\))?(?: \((.*)\))?$")
R_VIDEO = re.compile(r"^\[\d+\] vídeo (\d+): servidor (\d+) \(custo ([\d.]+)\)(?:, antes (\d+))?$")
R_FLUXO = re.compile(r"^\[\d+\] fluxo \((\d+), (\d+)\): pai=(\S+) destinos=(.*) ativo=(True|False)$")
R_VIZ = re.compile(r"^\[\d+\] tabela de vizinhos: (.*)$")
R_PING = re.compile(r"^PING (\d+) do vídeo \((\d+), (\d+)\): (.*)$")
R_SALTO = re.compile(r"(\d+)(?: \(\+([\d.]+) ms\))?")
R_BAIXO = re.compile(r"^\[\d+\] vizinho (\d+) em baixo \((.*)\)$")
R_SAIU = re.compile(r"^\[\d+\] vizinho (\d+) avisou que vai sair$")
R_VOLTOU = re.compile(r"^\[\d+\] vizinho (\d+) (?:voltou|ativo)$")
R_REINICIO = re.compile(r"^\[\d+\] (?:HELLO repetido de (\d+);|vizinho (\d+) reiniciou)")
R_PLAYER_RESUMO = re.compile(
    r"^player: (?:a sair: )?frame \d+ · recebidos (\d+) de (\d+) · em falta (\d+) \(([\d.]+) %\) · atrasados (\d+)$")
R_PLAYER_PARAGEM = re.compile(r"^player: (\d+) ms sem frame novo \((.*)\)$")
R_NOSH = re.compile(r"^no\.sh: (.*)$")
R_ARRANQUE = re.compile(r"^\[0\] à escuta em ")

# Estados de um nó, do melhor para o pior
ATIVO, A_ARRANCAR, SEM_SINAL, PARADO, MORTO, TERMINOU, ERRO = (
    "ativo", "a arrancar", "sem sinal", "parado (Ctrl+C)", "morto (kill -9)", "terminou", "erro")
EM_BAIXO = {SEM_SINAL, PARADO, MORTO, TERMINOU, ERRO}

# Tipos de acontecimento (o painel dá uma cor a cada um)
FALHA, RECUPERA, ACAO, VIDEO, ERRO_EV = "falha", "recupera", "acao", "video", "erro"


@dataclass
class Registo:
    hora: str
    nivel: str
    texto: str


@dataclass
class Fluxo:
    pai: str | None
    destinos: list[str]
    ativo: bool


@dataclass
class Player:
    recebidos: int = 0
    esperados: int = 0
    falta_pct: float = 0.0
    atrasados: int = 0
    hora: str = ""                     # hora do último resumo
    paragem: tuple[str, int] | None = None  # (hora, ms) da última paragem


@dataclass
class No:
    nome: str
    id: str = ""
    estado: str = A_ARRANCAR
    ultima_hora: str = ""
    pais: dict = field(default_factory=dict)      # servidor -> (pai ou None, custo)
    videos: dict = field(default_factory=dict)    # vídeo -> (servidor, custo)
    fluxos: dict = field(default_factory=dict)    # (servidor, vídeo) -> Fluxo
    vizinhos: list = field(default_factory=list)  # ids dos vizinhos ativos
    ping: tuple | None = None                     # (hora, [(id, ms ou None)], servidor, vídeo)
    cliente: bool = False
    player: Player | None = None

    @property
    def em_baixo(self) -> bool:
        return self.estado in EM_BAIXO


@dataclass
class Acontecimento:
    hora: str
    texto: str
    tipo: str


@dataclass
class Arvore:
    """Por onde passa um fluxo (servidor, vídeo): quem envia a quem, a partir do servidor."""
    servidor: str          # id
    video: str
    raiz: str              # nome do nó do servidor
    filhos: dict           # nome -> [nomes]
    ao_player: set         # nomes que entregam o vídeo ao player (clientes a ver)
    soltos: list           # nomes com o fluxo ativo mas fora da árvore (estado antigo nos logs)


@dataclass
class Estado:
    cenario: str
    nos: dict              # nome -> No
    nomes: dict            # id -> nome
    acontecimentos: list   # [Acontecimento], por ordem
    hora: str              # hora da última linha lida (ou o limite pedido)
    topologia: topologia.Topologia | None

    def nome(self, ident) -> str:
        if ident in (None, "None", "nenhum", ""):
            return "-"
        return self.nomes.get(str(ident), str(ident))

    def servidores(self) -> list[str]:
        """Ids dos servidores que aparecem nos logs (como pais ou como fluxos)."""
        ids = {s for n in self.nos.values() for s in n.pais} | {s for n in self.nos.values() for s, _ in n.fluxos}
        return sorted(ids, key=lambda s: ordem(self.nome(s)))


def ordem(nome: str):
    """Ordena S1, S2, N1, ..., C1, ..., e depois o resto."""
    m = re.match(r"([A-Za-z]+)(\d*)", nome)
    letra, num = (m.group(1), int(m.group(2) or 0)) if m else (nome, 0)
    return ({"S": 0, "N": 1, "C": 2}.get(letra[:1], 3), letra, num)


def segundos(hora: str) -> float:
    h, m, s = hora.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def ler(ficheiro: Path, ate: str | None = None) -> list[Registo]:
    registos, ultima = [], "00:00:00.000"
    try:
        texto = ficheiro.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return registos
    for linha in texto.splitlines():
        m = LINHA.match(linha)
        if m:
            if ate and m.group(1) > ate:
                break
            ultima = m.group(1)
            registos.append(Registo(m.group(1), m.group(2), m.group(3)))
        elif linha.startswith("Traceback"):
            registos.append(Registo(ultima, "ERROR", "erro no programa (Traceback no log)"))
    return registos


def carregar(cenario: str, ate: str | None = None):
    """{nó: registos do nó overlay} e {nó: registos do player}."""
    principais, players = {}, {}
    for f in sorted(LOGS.glob(f"cenario{cenario}-*.log")):
        partes = f.stem.split("-")[1:]  # cenario3-C1-player -> ["C1", "player"]
        if not partes:
            continue
        nome, sufixo = partes[0], (partes[1] if len(partes) > 1 else "")
        if sufixo == "player":
            players[nome] = ler(f, ate)
        elif sufixo not in SUFIXOS and not sufixo.startswith("video"):
            principais[nome] = ler(f, ate)
    return principais, players


def analisar(cenario: str, ate: str | None = None) -> Estado:
    """O estado da rede segundo os logs (até à hora `ate`, se for dada, no formato HH:MM:SS)."""
    if ate and len(ate) == 8:
        ate += ".999"
    principais, players = carregar(cenario, ate)
    topo = topologia.ler(cenario)
    nos = {nome: No(nome) for nome in principais}
    for nome, regs in principais.items():
        for r in regs:
            m = R_ID.match(r.texto)
            if m and m.group(1) != "0":  # antes do registo no bootstrapper o nó ainda é o 0
                nos[nome].id = m.group(1)
                break
    nomes = {}
    if topo:  # o número de cada máquina no CORE é o número do nó
        nomes.update({m.id: m.nome for m in topo.maquinas.values()})
    nomes.update({n.id: n.nome for n in nos.values() if n.id})
    estado = Estado(cenario, nos, nomes, [], ate[:12] if ate else "", topo)
    nm = estado.nome

    def traduzir(motivo: str) -> str:
        return re.sub(r"\b(vizinho|de|que) (\d+)\b", lambda m: f"{m.group(1)} {nm(m.group(2))}", motivo)

    eventos = []            # (hora, texto, tipo, chave para juntar, quem viu)
    relatos = {}            # id do nó -> [(hora, "baixo"/"cima")]
    ativos_para = set()     # (quem relata, sobre quem): já esteve ativo para quem relata
    em_baixo_para = set()   # (quem relata, sobre quem): deu-o como em baixo depois de ativo
    ultima = ""

    for nome, regs in principais.items():
        no = nos[nome]
        for r in regs:
            t = r.texto
            no.ultima_hora = r.hora
            ultima = max(ultima, r.hora)
            if R_ARRANQUE.match(t):  # processo novo: o que o anterior escreveu já não vale
                no.pais, no.videos, no.fluxos, no.vizinhos, no.ping = {}, {}, {}, [], None
                no.estado = A_ARRANCAR
            if no.estado == A_ARRANCAR and no.id and t.startswith(f"[{no.id}] "):
                no.estado = ATIVO
            if (m := R_PAI.match(t)):
                srv, pai, custo, motivo = m.groups()
                no.pais[srv] = (None if pai == "nenhum" else pai, custo)
                if motivo or pai == "nenhum":
                    texto = f"{nome}: pai para {nm(srv)} → {nm(pai) if pai != 'nenhum' else 'nenhum'}"
                    texto += f" (custo {custo})" if custo else ""
                    texto += f" — {traduzir(motivo)}" if motivo else ""
                    eventos.append((r.hora, texto, FALHA if pai == "nenhum" else RECUPERA, None, None))
            elif (m := R_VIDEO.match(t)):
                video, srv, custo, antes = m.groups()
                no.videos[video] = (srv, custo)
                no.cliente = True
                texto = f"{nome}: vídeo {video} de {nm(srv)} (custo {custo})"
                if antes:
                    texto += f", antes de {nm(antes)}"
                eventos.append((r.hora, texto, VIDEO, None, None))
            elif (m := R_FLUXO.match(t)):
                srv, video, pai, destinos, ativo = m.groups()
                lista = [] if destinos.startswith("(") else [d.strip() for d in destinos.split(",")]
                no.fluxos[(srv, video)] = Fluxo(None if pai == "None" else pai, lista, ativo == "True")
            elif (m := R_VIZ.match(t)):
                corpo = m.group(1)
                no.vizinhos = [] if corpo.startswith("(") else [v.split("@")[0] for v in corpo.split(", ")]
            elif (m := R_PING.match(t)):
                _n, srv, video, caminho = m.groups()
                saltos = [(i, float(ms) if ms else None) for i, ms in R_SALTO.findall(caminho)]
                no.ping = (r.hora, saltos, srv, video)
            elif (m := R_BAIXO.match(t)):
                outro, motivo = m.groups()
                relatos.setdefault(outro, []).append((r.hora, "baixo"))
                if (no.id, outro) in ativos_para:
                    em_baixo_para.add((no.id, outro))
                    eventos.append((r.hora, f"{nm(outro)} em baixo ({motivo})", FALHA, ("baixo", outro), nome))
            elif (m := R_SAIU.match(t)):
                outro = m.group(1)
                relatos.setdefault(outro, []).append((r.hora, "baixo"))
                em_baixo_para.add((no.id, outro))
                eventos.append((r.hora, f"{nm(outro)} saiu e avisou", FALHA, ("saiu", outro), nome))
            elif (m := R_VOLTOU.match(t)):
                outro = m.group(1)
                relatos.setdefault(outro, []).append((r.hora, "cima"))
                if (no.id, outro) in em_baixo_para:
                    em_baixo_para.discard((no.id, outro))
                    eventos.append((r.hora, f"{nm(outro)} voltou", RECUPERA, ("voltou", outro), nome))
                ativos_para.add((no.id, outro))
            elif (m := R_REINICIO.match(t)):
                outro = m.group(1) or m.group(2)
                relatos.setdefault(outro, []).append((r.hora, "cima"))
                eventos.append((r.hora, f"{nm(outro)} reiniciou", RECUPERA, ("reiniciou", outro), nome))
            elif (m := R_NOSH.match(t)):
                acao = m.group(1)
                if "Ctrl+C" in acao:
                    no.estado = PARADO
                elif "kill -9" in acao:
                    no.estado = MORTO
                elif "arrancado" in acao:
                    no.estado = A_ARRANCAR
                eventos.append((r.hora, f"{nome}: {acao}", ACAO, None, None))
            elif t == "nó OTT terminado":
                if no.estado not in (PARADO, MORTO):
                    no.estado = TERMINOU
            elif r.nivel == "ERROR":
                no.estado = ERRO
                eventos.append((r.hora, f"{nome}: erro — {t}", ERRO_EV, None, None))

    # Um nó que parece ativo mas que os vizinhos deram como em baixo depois da última linha dele
    # (ex: morto sem aviso, ou com as ligações cortadas) fica "sem sinal".
    for no in nos.values():
        if no.estado == ATIVO and no.id in relatos:
            hora, tipo = max(relatos[no.id])
            if tipo == "baixo" and hora >= no.ultima_hora:
                no.estado = SEM_SINAL

    for nome, regs in players.items():
        no = nos.setdefault(nome, No(nome))
        no.cliente = True
        no.player = Player()
        for r in regs:
            ultima = max(ultima, r.hora)
            if (m := R_PLAYER_RESUMO.match(r.texto)):
                rec, esp, _f, pct, atr = m.groups()
                no.player.recebidos, no.player.esperados = int(rec), int(esp)
                no.player.falta_pct, no.player.atrasados, no.player.hora = float(pct), int(atr), r.hora
            elif (m := R_PLAYER_PARAGEM.match(r.texto)):
                no.player.paragem = (r.hora, int(m.group(1)))
                eventos.append((r.hora, f"{nome}: vídeo parado {m.group(1)} ms ({m.group(2)})", FALHA, None, None))

    estado.acontecimentos = _juntar(eventos)
    estado.hora = estado.hora or ultima
    return estado


def _juntar(eventos) -> list[Acontecimento]:
    """Junta numa linha o mesmo aviso dado por vizinhos diferentes quase ao mesmo tempo."""
    saida = []  # [hora, texto, tipo, chave, [quem viu]]
    for hora, texto, tipo, chave, quem in sorted(eventos, key=lambda e: e[0]):
        if chave is not None:
            for e in reversed(saida[-12:]):
                if e[3] == chave and segundos(hora) - segundos(e[0]) <= JUNTAR_S:
                    if quem not in e[4]:
                        e[4].append(quem)
                    break
            else:
                saida.append([hora, texto, tipo, chave, [quem]])
        else:
            saida.append([hora, texto, tipo, None, []])
    return [Acontecimento(h, t + (f" — visto por {', '.join(q)}" if q else ""), tipo)
            for h, t, tipo, _c, q in saida]


def arvores(estado: Estado) -> list[Arvore]:
    """Uma árvore por fluxo ativo, a partir dos destinos que cada nó escreveu no log.

    A árvore é o que os logs dizem: um nó que morreu sem aviso continua com a última tabela que
    escreveu, até os vizinhos o darem como em baixo e escreverem a deles."""
    fluxos = sorted({f for n in estado.nos.values() for f, fl in n.fluxos.items() if fl.ativo},
                    key=lambda f: (ordem(estado.nome(f[0])), int(f[1])))
    saida = []
    for srv, video in fluxos:
        filhos, ao_player, com_fluxo = {}, set(), set()
        for no in estado.nos.values():
            fl = no.fluxos.get((srv, video))
            if fl is None or not fl.ativo:
                continue
            if no.videos and no.videos.get(video, (None,))[0] != srv:
                continue  # cliente que já trocou de servidor: a linha do fluxo antigo ficou no log
            com_fluxo.add(no.nome)
            # Só conta "envia a F" se o F também diz que este nó é o seu pai: um nó que morreu, ou
            # que ainda não soube que o F trocou de pai, fica com destinos velhos no log.
            destinos = [estado.nome(d) for d in fl.destinos if _pai_de(estado, estado.nome(d), srv) in (None, no.id)]
            if destinos:
                filhos[no.nome] = sorted(destinos, key=ordem)
            elif no.cliente or no.player:
                ao_player.add(no.nome)
        for no in estado.nos.values():  # o cliente nem sempre escreve a linha do fluxo
            if no.videos.get(video, (None,))[0] == srv and not no.em_baixo:
                ao_player.add(no.nome)
                com_fluxo.add(no.nome)
        raiz = estado.nome(srv)
        vistos, fila = {raiz}, [raiz]
        while fila:
            atual = fila.pop()
            for f in filhos.get(atual, []):
                if f not in vistos:
                    vistos.add(f)
                    fila.append(f)
        soltos = sorted(com_fluxo - vistos, key=ordem)
        saida.append(Arvore(srv, video, raiz, filhos, ao_player, soltos))
    return saida


def _pai_de(estado: Estado, nome: str, srv: str) -> str | None:
    """O pai que o próprio nó diz ter para o servidor (id), ou None se não se sabe."""
    no = estado.nos.get(nome)
    if no is None or srv not in no.pais:
        return None
    return no.pais[srv][0]


def texto_arvore(arv: Arvore, estado: Estado, pintar=lambda nome, texto: texto) -> list[str]:
    """A árvore em texto, com ├─ e └─. `pintar(nome, texto)` pode dar cor a cada nó.

    ▶ = entrega o vídeo ao player (cliente a ver); ✖ = nó em baixo segundo os logs."""
    def rotulo(nome: str) -> str:
        no = estado.nos.get(nome)
        texto = nome
        if nome in arv.ao_player:
            texto += " ▶"
        if no is not None and no.em_baixo:
            texto += f" ✖ {no.estado}"
        return pintar(nome, texto)

    linhas = [pintar(arv.raiz, f"{arv.raiz} · vídeo {arv.video}")]

    vistos = {arv.raiz}

    def ramo(nome: str, prefixo: str) -> None:
        filhos = [f for f in arv.filhos.get(nome, []) if f not in vistos]
        vistos.update(filhos)
        for i, f in enumerate(filhos):
            ultimo = i == len(filhos) - 1
            linhas.append(f"{prefixo}{'└─' if ultimo else '├─'} {rotulo(f)}")
            ramo(f, prefixo + ("   " if ultimo else "│  "))

    ramo(arv.raiz, "")
    if arv.soltos:
        linhas.append("  com o fluxo, mas sem caminho até ao servidor: " + ", ".join(
            pintar(n, n + (" ✖" if n in estado.nos and estado.nos[n].em_baixo else "")) for n in arv.soltos))
    return linhas
