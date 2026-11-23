# ESR TP2 — Serviço Over-the-Top para entrega de multimédia

Protótipo de um serviço OTT que entrega vídeo em tempo real a vários clientes através de uma rede overlay aplicacional, testado no emulador CORE.

Engenharia de Serviços em Rede · MEI · Universidade do Minho · 2026/27
Grupo: George, Inês, Pedro

## Estrutura

| Pasta | Conteúdo |
| --- | --- |
| `ott/` | Nó do overlay: transporte QUIC-like sobre UDP, vizinhos (HELLO), rotas e reencaminhamento dos fluxos |
| `bootstrapper/` | Dá a cada nó novo o seu número e os vizinhos. Só serve para a inicialização, não participa nas rotas |
| `player/` | Servidor e player de vídeo (adaptados do ProgEx) e o medidor. Correm à parte e falam com o nó local por UDP em localhost |
| `configs/` | JSON do bootstrapper, um por cenário (IP → número do nó, e as ligações) |
| `topologias/` | Topologias do CORE, uma por cenário (`cenario1.xml` a `cenario3.xml`) |
| `scripts/` | Arranque dos cenários, painel, `no.sh` (mexer em nós e ligações), testes do HELLO e análise das medições |
| `videos/` | Os 2 vídeos de teste |
| `docs/` | Documentação (tabela abaixo) |
| `tests/` | Testes automáticos (`python3 -m unittest`) |

| Documento | O que tem |
| --- | --- |
| [`docs/protocolo.md`](docs/protocolo.md) | Especificação do protocolo: pacotes, mensagens, vizinhos, rotas, fluxos e o que seguimos |
| [`docs/testes.md`](docs/testes.md) | Como arrancar os cenários, mexer nos nós e nas ligações e medir |
| [`docs/enderecos.md`](docs/enderecos.md) | IPs e ligações de cada cenário |
| [`docs/resultados-medicoes.md`](docs/resultados-medicoes.md) | Resultados com e sem overlay (cenário 3, 1 a 3 clientes); os dados estão em `docs/medicoes/` |
| [`docs/frames.md`](docs/frames.md) | Tamanho dos frames do vídeo e efeito da perda |
| [`docs/plano-da-equipa.md`](docs/plano-da-equipa.md) | O que está feito, o que falta e quem faz o quê |
| [`docs/extras.md`](docs/extras.md) | Estado de cada extra (o que o guião não pede) |

## Decisões base

- Python 3, só com a biblioteca standard (exceto o player, que precisa do Pillow: ver "Requisitos")
- asyncio no nó `ott`; o player corre num processo separado
- UDP com um protocolo próprio inspirado no QUIC: um socket por nó, controlo fiável (ACK e retransmissão), vídeo sem retransmissão
- Cabeçalho binário (`struct`), corpo das mensagens de controlo em JSON, vídeo em bytes crus

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

```
python3 -m unittest -v                                   # testes automáticos
sudo -E bash scripts/arrancar_cenario.sh 3               # cenário 3 inteiro no CORE (depois do Start)
```

Cada programa à parte:

```
python3 -m bootstrapper configs/cenario2.json                     # bootstrapper
python3 -m ott <ip-do-bootstrapper>                               # nó intermédio
python3 -m ott <ip-do-bootstrapper> --servidor --servidor         # nó do servidor: um --servidor por vídeo (portas 24000, 24001, ...)
python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000     # vídeo 0 para o nó local (movie2.Mjpeg na 24001 para o vídeo 1)
python3 -m ott <ip-do-bootstrapper> --player --ver 0              # nó do cliente: pede o vídeo 0 e entrega-o ao player (porta 25000)
python3 player/Cliente.py                                         # player
```

- `--ver k`: pede o vídeo k ao servidor mais barato entre os que o anunciam (os servidores têm os
  mesmos vídeos pela mesma ordem). Se esse servidor ficar sem rota, o cliente passa para o outro.
- `--ver <servidor>:<vídeo>` (ex: `--ver 1:0`): pede o vídeo a esse servidor.
- `--porta P`: porta do bootstrapper, se não for a 5000 (no cenário 3 é a 5001).

Os comandos de cada cenário, nó a nó, estão em [`docs/testes.md`](docs/testes.md).

## Regras de trabalho

- Um ramo por pessoa (ex: `george/...`), junto à `main` por pull request quando estiver testado. Antes de continuar a trabalhar, juntar a `main` ao ramo.
- Não fazer commit de capturas (`.pcap`), logs nem vídeos grandes. Os logs dos testes vão para `logs/` (ignorada pelo git); os resultados que entram no relatório ficam em `docs/medicoes/`.
- Cada coisa na sua pasta: código em `ott/` e `bootstrapper/`, topologias em `topologias/`, scripts de apoio em `scripts/`.
- Quem usar um termo técnico novo num documento explica-o no próprio documento.
