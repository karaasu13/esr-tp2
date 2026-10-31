# Tamanho dos frames do `movie.Mjpeg`

Resultados de `scripts/medir_frames.py` sobre o vídeo de teste do ProgEx.

```
python3 scripts/medir_frames.py movie.Mjpeg
```

## O vídeo

O `movie.Mjpeg` é uma sequência de 500 imagens JPEG independentes (frames). No ficheiro, cada frame vem precedido de 5 bytes ASCII com o seu tamanho; é assim que o `VideoStream.py` do ProgEx o lê. O `Servidor.py` envia um frame de 50 em 50 ms (20 fps), cada frame inteiro num só pacote RTP/UDP.

O script lê o ficheiro da mesma forma, mas em vez de enviar os frames só regista o tamanho de cada um. **Não envia nada pela rede**: os valores de perda abaixo são uma estimativa calculada, não um teste no CORE.

## Tamanho dos frames

| | |
|---|---|
| Frames | 500 |
| Mínimo | 4970 B |
| Média | 8535 B |
| Mediana | 8132 B |
| Máximo | 13967 B |

## Cabe num pacote?

Cada frame vai num pacote com estes cabeçalhos:

| Camada | Bytes |
|---|---|
| IP | 20 |
| UDP | 8 |
| Overlay (o nosso, ver `protocolo.md`) | 14 |
| RTP (ProgEx) | 12 |
| **Total** | **54** |

Com MTU de 1500 B, um frame só cabe num pacote se tiver **≤ 1446 B**. Nenhum dos 500 frames cabe: **100 % são fragmentados pelo IP**.

| Fragmentos por frame | Frames |
|---|---|
| 4 | 15 |
| 5 | 155 |
| 6 | 148 |
| 7 | 110 |
| 8 | 35 |
| 9 | 30 |
| 10 | 7 |
| **Média** | **6,23** |

(Cada fragmento leva o seu cabeçalho IP e até 1480 B de dados.)

## Efeito da perda

O IP só entrega o pacote se chegarem **todos** os fragmentos. Um fragmento perdido estraga o frame inteiro. Para um frame com *k* fragmentos e perda *p* por pacote:

```
P(frame perdido) = 1 − (1 − p)^k
```

Média sobre os 500 frames:

| Perda por pacote | Frames perdidos (estimativa) |
|---|---|
| 5 % | ~27 % |
| 10 % | ~48 % |
| 20 % | ~74 % |
| 30 % | ~88 % |

A perda de vídeo é muito maior do que a perda da rede. Isto vem da fragmentação, não do protocolo overlay, e deve ser tido em conta ao ler os resultados dos testes de perda (`testes.md`).

Assume perda independente em cada fragmento e num só link; com vários links com perda no caminho, as perdas acumulam.

## Opções para o envio

1. **Manter 1 frame = 1 pacote** (como o ProgEx). O IP fragmenta sozinho. É mais simples; o relatório explica a perda com esta tabela.
2. **Partir o frame na aplicação** em pedaços de ≤ 1446 B, cada um com o nº do frame e o nº do pedaço, e o cliente junta-os. Mais trabalho, mas o nó sabe exatamente que pedaço se perdeu e pode, por exemplo, mostrar o frame anterior ou pedir só esse pedaço.

Decisão em aberto, a tomar em grupo.

## Próximo passo

Quando o nó `ott` já reencaminhar vídeo: aplicar perda com `tc`, contar os frames que chegam ao cliente e comparar com a tabela acima.
