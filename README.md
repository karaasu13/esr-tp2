# ESR TP2 — Serviço Over-the-Top para entrega de multimédia

Protótipo de um serviço OTT que entrega vídeo em tempo real a vários clientes através de uma rede overlay aplicacional, testado no emulador CORE.

Engenharia de Serviços em Rede · MEI · Universidade do Minho · 2026/27
Grupo: George, Inês, Pedro

## Estrutura

| Pasta | Conteúdo |
| --- | --- |
| `ott/` | Nó overlay: transporte QUIC-like sobre UDP, vizinhos, rotas e reencaminhamento dos fluxos |
| `bootstrapper/` | Regista os nós e devolve-lhes o ID e os vizinhos. Só serve para a inicialização, não participa nas rotas |
| `player/` | Player de vídeo (adaptado do ProgEx). Corre num processo à parte e recebe do nó local por UDP em localhost |
| `configs/` | Ficheiros JSON do bootstrapper, um por cenário (IP → ID e lista de ligações) |
| `topologias/` | Topologias do CORE (`.xml`) |
| `scripts/` | Scripts auxiliares para correr testes e demos no CORE (ex: `demo-cenario1.sh`) |
| `docs/` | Especificação do protocolo, endereços, guia de testes e os documentos do grupo: plano (`plano-da-equipa.md`), desenho das rotas e do vídeo (`desenho-rotas-e-video.md`) e extras (`extras.md`) |
| `tests/` | Testes automáticos (`python3 -m unittest`) |

## Decisões base

- Python 3, só com a biblioteca standard (exceto o player, que precisa do Pillow: ver "Requisitos")
- asyncio no nó `ott`; o player corre num processo separado
- UDP com um protocolo próprio inspirado no QUIC: um socket por nó, controlo fiável (ACK e retransmissão), vídeo sem retransmissão
- Cabeçalho binário (`struct`), corpo das mensagens de controlo em JSON, vídeo em bytes crus

O porquê de cada decisão e as alternativas estão no documento "TP2 ESR — Plano e decisões".

## Requisitos

O nó OTT, o bootstrapper e o `Servidor.py` só usam a biblioteca standard. O player
(`player/Cliente.py`) precisa também do **Tkinter** e do **Pillow** (`PIL.ImageTk`), que não vêm
com o Python. Os nós do CORE usam o Python da própria máquina, por isso basta instalar uma vez
na máquina (ou VM) onde corre o CORE:

```
sudo apt install python3-tk python3-pil python3-pil.imagetk   # Debian/Ubuntu
python3 -c "from PIL import ImageTk; import tkinter"          # confirma: não deve dar erro
```

Em Debian/Ubuntu o `ImageTk` vem num pacote à parte (`python3-pil.imagetk`): só com o
`python3-pil`, o player falha com `ImportError: cannot import name 'ImageTk'`. Fora do apt,
`pip install pillow` também serve.

## Como correr

Já existe:

```
python3 -m unittest -v                                      # testes automáticos
python3 -m ott.demo_transporte --id 2 1=10.0.0.10 3=10.0.1.20   # demo no CORE
python3 -m bootstrapper configs/cenario2.json               # bootstrapper
python3 -m ott <ip-do-bootstrapper>                         # nó OTT
```

O bootstrapper identifica cada nó pelo IP de origem do `REGISTER` e responde com o ID e os
vizinhos definidos em `links`. O nó OTT regista-se, faz o handshake `HELLO`/`HELLO_ACK` e
mantém a tabela de vizinhos atualizada, removendo vizinhos que caem e readmitindo-os quando
voltam a comunicar.

No cenário 2, se o bootstrapper estiver no n7, usa um IP alcançável desse router, por exemplo
`python3 -m ott 10.0.1.1` no N1.

### Vídeo nas pontas (UDP em localhost)

```
python3 player/Servidor.py videos/movie.Mjpeg [ip] [porta]   # envia ao nó local, 127.0.0.1:24000 por omissão (24001 para o 2.º vídeo)
python3 -m ott <ip-do-bootstrapper> --servidor        # nó do servidor: escuta o Servidor.py (um --servidor por vídeo: 24000, 24001, ...)
python3 -m ott <ip-do-bootstrapper> --player          # nó do cliente: entrega ao player, 127.0.0.1:25000
python3 player/Cliente.py                             # player
```

Para um 2.º vídeo que se distinga à vista, há o `videos/movie2.Mjpeg` (um clip de 5 s da Pexels,
102 frames de 384×216, que recomeça no fim): `python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001`.

O pedido local de vídeo faz-se com `--ver`:

- `--ver k` (ex: `--ver 0`): pede o vídeo k ao servidor mais barato entre os que o anunciam. Os
  servidores têm os mesmos vídeos pela mesma ordem. Se esse servidor ficar sem rota, o cliente
  passa para o outro (LEAVE do fluxo antigo, JOIN do novo); se o primeiro voltar e for mais
  barato pela margem (15% e 2 unidades), volta para ele.
- `--ver <ID do servidor>:<vídeo>` (ex: `--ver 1:0`): pede o vídeo a esse servidor.

O nó do servidor anuncia-se de 2 em 2 s, cada nó escolhe o pai pelo custo mais baixo e, assim que
o cliente tem pai, o `JOIN` sobe sozinho até ao servidor.

Para arrancar um cenário inteiro de uma vez (bootstrapper, nós, players e servidores, uma janela
por programa, logs em `logs/`): `sudo -E bash scripts/arrancar_cenario.sh 2` ou `... 3` (ver
`docs/testes.md`).

### Cenário 2 no CORE (`topologias/testte2.xml`)

Na pasta do projeto, em cada nó. O bootstrapper corre no n7; os nós usam o IP do n7 do lado deles
(`10.0.0.1` no S1, `10.0.1.1` no N1, `10.0.2.1` no N2); os nós ligados ao n8 usam `10.0.6.1`, a
interface do n7 virada para o n8. Arranca o nó do S1 por último, para o 1.º anúncio já encontrar
as ligações feitas.

```
n7:  python3 -m bootstrapper configs/cenario2.json
N1:  python3 -m ott 10.0.1.1
N2:  python3 -m ott 10.0.2.1
N3:  python3 -m ott 10.0.6.1
C1:  python3 -m ott 10.0.6.1 --player --ver 1:0
C1:  python3 player/Cliente.py              # 2.º terminal
C2:  python3 -m ott 10.0.6.1 --player --ver 1:0
C2:  python3 player/Cliente.py              # 2.º terminal
S1:  python3 player/Servidor.py videos/movie.Mjpeg
S1:  python3 -m ott 10.0.0.1 --servidor     # 2.º terminal
```

No log de cada nó aparece `pai para o servidor 1: <vizinho> (custo X)`. Pais esperados: N1 → 1,
N2 → 2, N3 → 2, C1 → 3, C2 → 4. O servidor regista um `PING` de 5 em 5 s e cada nó do caminho
regista-o com o caminho percorrido.

### Cenário 3 no CORE (`topologias/underlay.xml`), com dois servidores

O bootstrapper corre no PC do N4 (`10.0.21.20`) na porta 5001, com uma cópia do JSON com essa
porta (é o que o script faz). Os dois servidores têm os mesmos 2 vídeos e os clientes pedem o
vídeo 0, sem dizer a que servidor:

```
N4:  sed 's/"port": 5000/"port": 5001/' configs/cenario3.json > /tmp/cenario3-bootstrapper.json
N4:  python3 -m bootstrapper /tmp/cenario3-bootstrapper.json
N1, N2, N3, N4, N5, N6:  python3 -m ott 10.0.21.20 --porta 5001
C1 a C5:  python3 player/Cliente.py
C1 a C5:  python3 -m ott 10.0.21.20 --porta 5001 --player --ver 0
S1, S2:   python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000    # vídeo 0
S1, S2:   python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001   # vídeo 1
S1, S2:   python3 -m ott 10.0.21.20 --porta 5001 --servidor --servidor
```

No log de cada cliente aparece `vídeo 0: servidor 17 (custo X)` (S1) ou `servidor 16` (S2), conforme
o que estiver mais perto. Quando um servidor para, os clientes dele mostram
`vídeo 0: servidor <outro> (custo X), antes <o que parou>`.

Especificação do protocolo: [`docs/protocolo.md`](docs/protocolo.md).

## Regras de trabalho

- Ninguém faz push direto para `main`: cada tarefa num ramo (ex: `george/transporte`) e pull request.
- Não fazer commit de capturas (`.pcap`), logs nem vídeos grandes. Os logs dos testes vão para `logs/` (ignorada pelo git).
- Cada coisa na sua pasta: código em `ott/` e `bootstrapper/`, topologias em `topologias/`, scripts de apoio em `scripts/`.
