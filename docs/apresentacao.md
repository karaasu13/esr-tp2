# Guião da apresentação (cenário 3, arranque à mão)

Na apresentação não se usam scripts: cada nó arranca à mão, na sua shell do CORE, um a um. Cada nó
precisa de um só comando. Os scripts (`arrancar_cenario.sh`, `no.sh`, medições) ficam para os
testes do relatório.

## Antes de começar

1. Abrir `topologias/cenario3.xml` no CORE e carregar em Start.
2. No PC, fora do CORE, uma vez: `xhost +SI:localuser:root` (para os players poderem abrir janela).
3. Esperar que os routers encaminhem (uns 30 s). Para confirmar, na shell do S1:
   `ping -c 2 10.0.26.20` (o C5).
4. Em cada shell de nó, primeiro `cd` para a pasta do projeto.

## Números dos nós

Os logs mostram os números que o bootstrapper deu (os nomes estão no início de cada linha):

| S1 | S2 | N1 | N2 | N3 | N4 | N5 | N6 | C1 | C2 | C3 | C4 | C5 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 17 | 16 | 13 | 20 | 11 | 15 | 10 | 18 | 14 | 12 | 8 | 19 | 21 |

## Arranque, por esta ordem

| Onde | Comando | O que se vê no log |
|---|---|---|
| N4 | `python3 -m bootstrapper configs/cenario3.json` | `à escuta` e, a cada nó que entra, o registo dele |
| N4 (2.ª shell), N1, N2, N3, N5, N6 | `python3 -m ott 10.0.21.20:5001` | `registo aceite`, depois `vizinho X ativo` e a `tabela de vizinhos` |
| C1 a C5 | `python3 -m ott 10.0.21.20:5001 --ver 0` | o mesmo, e abre-se o player (ainda sem imagem) |
| S1, S2 | `python3 -m ott 10.0.21.20:5001 --videos movie movie2` | `arrancado: Servidor.py (vídeo 0…)` e `(vídeo 1…)` |

Uns segundos depois de arrancarem os servidores:

- em cada nó: `pai para o servidor 17: <vizinho> (custo X)` e o mesmo para o 16;
- em cada cliente: `vídeo 0: servidor 17 (custo X)` (ou 16), e a imagem aparece no player;
- de 5 em 5 s, nos nós do caminho: `PING n do vídeo (17, 0): 17 → 13 (+0.8 ms) → 14 (+0.6 ms)`.

## O que mostrar

**Tabela de rotas e cópia única.** Cada mudança escreve a tabela de fluxos do nó:
`fluxo (17, 0): pai=17 custo=1.6 destinos=14 ativo=True` (fluxo, origem, custo, destinos, estado,
como o guião pede). No S1, o fluxo (17, 0) tem como destinos só os vizinhos que pediram, uma cópia
por vizinho, mesmo que por trás haja vários clientes.

**Um nó sai com aviso.** `Ctrl+C` na shell do N1. Os vizinhos mostram `vizinho 13 avisou que vai
sair` e o C1 troca logo para o N3 (`pai para o servidor 17: 11 … (vizinho 13 em baixo)`).

**Um nó morre sem aviso.** `Ctrl+\` na shell do N1 (mata o nó sem lhe dar tempo de avisar; os
programas que ele arrancou morrem com ele). O C1 troca de pai em menos de meio segundo, pela falta
de vídeo (`… ms sem vídeo de 13`); uns 3 s depois os vizinhos dão o N1 como em baixo pelo silêncio.

**O nó volta.** Na shell do N1, seta para cima e Enter. Os vizinhos mostram `vizinho 13 voltou`.

**Uma ligação piora.** Na shell do N1: `tc qdisc add dev eth0 root netem delay 50ms`. O custo
pelo N1 sobe e os nós abaixo dele passam para outro caminho (`melhor do que 13, custo …`). Para
tirar: `tc qdisc del dev eth0 root`. (A `eth0` de um PC leva todas as ligações desse nó.)

**Um servidor cai.** `Ctrl+C` na shell do S1. Os clientes que viam o S1 mostram
`vídeo 0: servidor 16 (custo X), antes 17` e o vídeo continua a partir do S2. (Problema conhecido:
a imagem pode ficar parada cerca de 2 s, porque os dois servidores vão em frames diferentes.)

## No fim

`Ctrl+C` em cada shell (ou Stop no CORE). Os servidores de vídeo e os players fecham com o nó.
