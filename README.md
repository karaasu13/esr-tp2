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

## Decisões base

- Python 3, só com a biblioteca standard
- asyncio no nó `ott`; o player corre num processo separado
- UDP com um protocolo próprio inspirado no QUIC: um socket por nó, controlo fiável (ACK e retransmissão), vídeo sem retransmissão
- Cabeçalho binário (`struct`), corpo das mensagens de controlo em JSON, vídeo em bytes crus

O porquê de cada decisão e as alternativas estão no documento "TP2 ESR — Plano e decisões".

## Como correr

Já existe:

```
python3 -m unittest -v                                      # testes do transporte
python3 -m ott.demo_transporte --id 2 1=10.0.0.10 3=10.0.1.20   # demo no CORE
```

Previsto:

```
python3 -m bootstrapper configs/cenario2.json
python3 -m ott <ip-do-bootstrapper>
```

Especificação do protocolo: [`docs/protocolo.md`](docs/protocolo.md).

## Regras de trabalho

- Ninguém faz push direto para `main`: cada tarefa num ramo (ex: `george/transporte`) e pull request.
- Não fazer commit de capturas (`.pcap`), logs nem vídeos grandes.
