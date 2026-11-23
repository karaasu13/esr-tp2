# Resultados das medições: com e sem overlay

Medições no cliente para comparar a nossa solução (vídeo pelo overlay) com a solução sem overlay
(o servidor envia uma cópia diretamente a cada cliente). Como se mede e o que significa cada
número: `docs/testes.md`, secção 4.

Faltam os **testes de falhas** (piorar uma ligação com `tc`, cortar a ligação do pai). Ficam para
depois de fechar a robustez das rotas (custo com as quatro medidas, troca com margem, WITHDRAW e
reparação), porque é esse código que eles medem.

## Condições

| | |
|---|---|
| Data | 17/11, entre as 16:28 e as 16:43 |
| Código | ramo `pedro/modo-medicao`, commit `8d8a1a6` (o protocolo é o da `main` em `ba0b8c8`) |
| Topologia | cenário 3 (`topologias/cenario3.xml`), no CORE |
| Servidor | só o S1, com o vídeo 0 (`videos/movie.Mjpeg`); o S2 não arrancou |
| Clientes | C1; C1 e C2; C1, C2 e C3 |
| Duração | 60 s de medição no S1, depois de todos os clientes arrancarem |
| Ligações | sem `tc`: nenhuma perda ou atraso artificial |
| Comando | `sudo -E bash scripts/arrancar_cenario.sh 3 --medir overlay\|direto --clientes K` |

No CORE todos os nós partilham o relógio do PC, por isso o atraso (hora de chegada menos a hora
de envio, que vai no timestamp RTP) é o atraso real de ponta a ponta.

## Resultados

Saída da `eth0` do S1 (`scripts/debito_interface.py`, média dos 60 s):

| Clientes | Sem overlay | Com overlay | Com overlay / sem overlay |
|---|---|---|---|
| 1 | 1308 kbit/s (116 pac/s) | 1357 kbit/s (129 pac/s) | 104 % |
| 2 | 2642 kbit/s (234 pac/s) | 2296 kbit/s (212 pac/s) | 87 % |
| 3 | 4015 kbit/s (356 pac/s) | 2596 kbit/s (239 pac/s) | 65 % |

No cliente (`scripts/analisar_medicoes.py`; com vários clientes, o intervalo entre o menor e o
maior):

| Clientes | | Atraso médio | Atraso p95 | Jitter | Perda | 1.º frame |
|---|---|---|---|---|---|---|
| 1 | sem overlay | 2,3 ms | 4,1 ms | 0,77 ms | 0 % | 2,1 s |
| 1 | com overlay | 6,0 ms | 9,1 ms | 1,32 ms | 0 % | 8,6 s |
| 2 | sem overlay | 2,4–2,5 ms | 4,6 ms | 0,71–0,72 ms | 0 % | 2,0–2,3 s |
| 2 | com overlay | 5,2–7,1 ms | 7,9–10,5 ms | 1,17–1,32 ms | 0 % | 6,4–6,7 s |
| 3 | sem overlay | 2,2–2,3 ms | 4,4–4,6 ms | 1,03–1,15 ms | 0 % | 2,0–2,1 s |
| 3 | com overlay | 4,8–7,1 ms | 7,5–10,3 ms | 1,45–1,73 ms | 0 % | 6,7–8,5 s |

Em todos os testes, todos os clientes receberam ~1250 a 1320 kbit/s de vídeo, a 18,7–18,9
frames/s, sem nenhum corte (intervalo sem frames maior do que 300 ms).

Os ficheiros de onde vêm os números estão em `docs/medicoes/cenario3-<modo>-<K>cli/`
(`analise.txt` e `S1-debito.txt`). Os CSV por frame e os logs dos nós ficaram em
`logs/medicoes/` no PC do Pedro (a pasta `logs/` não vai para o git).

## Conclusões

**O overlay poupa o servidor.** Sem overlay, o tráfego à saída do S1 cresce com o número de
clientes (1×, 2×, 3×), porque o S1 envia uma cópia a cada um. Com overlay, o S1 envia no máximo
uma cópia **por vizinho no overlay**, e não por cliente. No cenário 3 o S1 tem 2 vizinhos (N1 e
N2), por isso nunca passa de 2 cópias: com 3 clientes, gasta 35 % menos do que sem overlay, e
com mais clientes continuaria nas 2 cópias. O log do S1 confirma: com 2 e com 3 clientes, o fluxo
do vídeo 0 tem os destinos N1 e N2.

**Com 1 cliente o overlay gasta um pouco mais** (+49 kbit/s, +13 pac/s): são as mensagens de
controlo (anúncios, sinais de vida, JOIN), que não existem sem overlay. Com 2 clientes, cada um
chega por um vizinho diferente do S1, por isso continuam a sair 2 cópias; a poupança é pequena.

**O preço é pouco atraso.** O vídeo passa pelos nós overlay do caminho (programas em Python), o que
acrescenta 3 a 5 ms de atraso médio e um pouco mais de jitter. A qualidade não muda: nenhuma
perda, nenhum corte, o mesmo débito e o mesmo número de frames por segundo.

**O arranque é mais lento** (6 a 9 s, contra 2 s). Com overlay, o cliente tem de se registar no
bootstrapper, apresentar-se aos vizinhos, esperar duas rondas de anúncios antes da 1.ª escolha do
servidor e pedir o vídeo (JOIN) pelo caminho escolhido. Sem overlay, o servidor já está a enviar.

## Observações

- Nos logs do S1 sem overlay (`S1-video-C*.log`) há erros `Network is unreachable`. Aparecem
  todos nas últimas ~15 a 30 linhas, depois do fim da medição, quando se fez Stop no CORE e a rede
  desapareceu. Não afetam os resultados.
- No teste com overlay e 2 clientes, o C2 trocou de pai (de N4 para N3, "485 ms sem vídeo de N4")
  às 16:36:44, depois de a medição ter acabado (16:36:36). Não entra nos números.
- O CSV do teste com overlay começa num frame acima de 1 (ex: 236): o S1 já estava a enviar antes
  de o cliente entrar. A perda conta só a partir do 1.º frame recebido.
- A pasta `logs/medicoes/cenario3-overlay-1cli.20261117-162817` está vazia: é uma 1.ª tentativa
  que não chegou a medir.

## Quando repetir

- **Os testes sem overlay** só se repetem se mudar o `Servidor.py`, o `RtpPacket.py`, o vídeo ou
  a topologia.
- **Os testes com overlay** repetem-se se mudar o caminho do vídeo (formato do pacote,
  reencaminhamento, escolha do pai) ou o tráfego de controlo (intervalo dos anúncios, sinais de
  vida, medidas do custo). Para o relatório, os números devem ser da versão final.
