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
| `docs/` | Especificação do protocolo |
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
python3 -m unittest -v                                      # testes do transporte
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
python3 -m ott <ip-do-bootstrapper> --servidor        # nó do servidor: escuta o Servidor.py (repetir --servidor PORTA por vídeo)
python3 -m ott <ip-do-bootstrapper> --player          # nó do cliente: entrega ao player, 127.0.0.1:25000
python3 player/Cliente.py                             # player
```

Para um 2.º vídeo que se distinga à vista, há o `videos/movie2.Mjpeg` (o mesmo com as cores
invertidas): `python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001`.

Enquanto não há rotas (Etapa 2), o caminho entre os nós configura-se à mão: no nó do servidor
`--enviar-para <ID do vizinho>` e no do cliente `--ver <ID do servidor>:<vídeo>` (ex: `--ver 1:0`).

Especificação do protocolo: [`docs/protocolo.md`](docs/protocolo.md).

## Regras de trabalho

- Ninguém faz push direto para `main`: cada tarefa num ramo (ex: `george/transporte`) e pull request.
- Não fazer commit de capturas (`.pcap`), logs nem vídeos grandes. Os logs dos testes vão para `logs/` (ignorada pelo git).
- Cada coisa na sua pasta: código em `ott/` e `bootstrapper/`, topologias em `topologias/`, scripts de apoio em `scripts/`.
