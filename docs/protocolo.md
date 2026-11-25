# Protocolo OTT — especificação

O protocolo dos nós do overlay: formato dos pacotes, mensagens, vizinhos, rotas, fluxos e fiabilidade.
É um protocolo próprio sobre UDP, inspirado no QUIC. Cada nó usa **um só socket UDP**, na porta
**5000**, para todos os vizinhos e para o bootstrapper.

Código: `ott/protocolo.py` (formato), `ott/transporte.py` (fiabilidade, medidas e vizinhos vivos),
`ott/rotas.py` (anúncios e escolha do pai) e `ott/__main__.py` (registo, HELLO, fluxos e PING).
De onde vem cada ideia está em "O que seguimos"; os termos técnicos estão no **Glossário**, no fim.

## Formato do pacote

```
 0        1        2        3
+--------+--------+--------+--------+
| versão |  tipo  | flags  | origem…|
+--------+--------+--------+--------+
|…origem |     destino     |  pn…   |
+--------+--------+--------+--------+
|          …pn             |servidor|
+--------+--------+--------+--------+
|…servid.| stream |  [id da mensagem: 4 B, só se FIAVEL]  |  corpo…
+--------+--------+
```

Cabeçalho fixo de **14 bytes**, ordem de rede (`struct "!BBBHHIHB"`).

| Campo | Tam. | Significado | Equivalente no QUIC |
|---|---|---|---|
| versão | 1 B | `1`. Pacotes com outra versão são descartados | Version |
| tipo | 1 B | Tipo de mensagem (tabela abaixo) | frame type |
| flags | 1 B | bit 0 `FIAVEL`: o recetor tem de responder com ACK. Restantes bits reservados | ack-eliciting |
| origem | 2 B | ID de quem envia. `0` = ainda sem ID (só antes do registo) | Source CID |
| destino | 2 B | ID do destinatário. `0` = bootstrapper. Um nó com ID descarta pacotes que não são para si | Destination CID |
| nº pacote (pn) | 4 B | Contador do nó, **nunca reutilizado**, nem nas retransmissões. Começa num valor aleatório (como o ISN do TCP), para um nó reiniciado não repetir ids que os vizinhos ainda guardam | Packet Number |
| fluxo: servidor | 2 B | ID do servidor de origem do fluxo. `0` = controlo | Stream ID |
| fluxo: stream | 1 B | Qual dos vídeos desse servidor | Stream ID |
| id da mensagem | 4 B | **Só em pacotes fiáveis.** Identifica a mensagem: é o pn do 1.º envio e mantém-se nas retransmissões | stream offset |

**Corpo:** JSON (UTF-8) nas mensagens de controlo, bytes crus no `DATA` (o pacote RTP do ProgEx, que já traz o nº de sequência e o timestamp).

**Fora do cabeçalho, de propósito:** o tamanho (o UDP já entrega o datagrama inteiro), o checksum (o UDP já tem) e o nº de sequência do vídeo (já vem no RTP).

## Tipos de mensagem

| Código | Tipo | Fiável | Corpo | Quem trata |
|---|---|---|---|---|
| 0x01 | REGISTER | sim | `{}` | bootstrapper |
| 0x02 | REGISTER_ACK | sim | `{"id": 3, "vizinhos": [{"id": 2, "ip": "10.0.0.1"}, …]}` | nó (registo) |
| 0x03 | HELLO | pede ACK, não é retransmitido (periódico) | `{"arranque": 2937415, "intervalo": 1.0, "ouvidos": [2, 5]}` | vizinhos |
| 0x06 | ACK | não | pn confirmado, 4 B binário | transporte |
| 0x07 | CLOSE | sim | `{}` | nó vizinho |
| 0x10 | ANNOUNCE | não | `{"srv": 1, "ronda": 1731600000, "videos": [0], "rtt": 2.0, "jitter": 0.1, "perda": 0.0, "saltos": 1}` | escolha de caminhos |
| 0x11 | JOIN | sim no pedido inicial; não nas renovações | vazio, ou `{"recuperar": true}` (ver "Falta de vídeo do pai"); fluxo no cabeçalho | gestão de fluxos |
| 0x12 | LEAVE | sim | vazio; fluxo no cabeçalho | gestão de fluxos |
| 0x13 | WITHDRAW | sim | `{"srv": 1}` | escolha de caminhos |
| 0x14 | PING | não | `{"n": 12, "caminho": [{"id": 17, "t": 1731245000.123}, …]}` | teste do caminho |
| 0x20 | DATA | não | bytes do vídeo | streaming |

Os códigos 0x04 e 0x05 estão livres: eram o HELLO_ACK e o KEEPALIVE, que o HELLO periódico substituiu.

## Vizinhos: o HELLO periódico

Uma só mensagem, o `HELLO`, faz de apresentação, de sinal de vida e de nova tentativa de
ligação, como o Hello do OSPF (RFC 2328) e os pacotes de controlo do BFD (RFC 5880). Cada nó
manda-o a cada vizinho configurado, a um ritmo que depende do estado desse vizinho:

| Estado do vizinho | O que quer dizer | Intervalo entre HELLOs |
|---|---|---|
| a arrancar | ainda não se ouviu nenhum HELLO dele | 1 s nos 3 primeiros, depois 5 s |
| a ouvir | ouvem-se os HELLOs dele, mas ele ainda não nos ouve (o Init do OSPF) | 1 s nos 3 primeiros, depois 5 s |
| vivo | ouvem-se os dois: o HELLO dele traz-nos em `ouvidos` | 1 s |
| em baixo | estava vivo e calou-se (ou deixou de nos ouvir) | 1 s nos 3 primeiros, depois 5 s |
| saiu | avisou com `CLOSE` | não se envia: é ele que se apresenta quando voltar |

O corpo do HELLO:

- `arranque`: número sorteado quando o nó arranca. Se mudar, o vizinho reiniciou: o estado
  antigo dele (fluxos e anúncios) é limpo, mesmo que não tenha chegado a ser dado como em baixo.
- `intervalo`: de quanto em quanto tempo esse nó manda HELLOs a um vizinho vivo (1 s). Quem o
  recebe dá-o como em baixo ao fim de 3 intervalos sem ouvir nada dele (ver "Vizinhos vivos").
- `ouvidos`: os vizinhos de quem esse nó está a ouvir HELLOs. Um vizinho só passa a vivo
  quando os dois se ouvem, e um vizinho vivo que deixa de nos pôr em `ouvidos` volta a "a ouvir".
  Assim, uma ligação que só funciona num sentido fica em baixo, em vez de ligar e desligar.

O HELLO pede ACK mas não é retransmitido: o seguinte faz esse papel. Os ACK dão as medidas da
ligação (RTT, jitter e perda). Logo que um vizinho muda de estado, o nó responde de imediato,
sem esperar pelo intervalo, para a ligação se formar em pouco mais do que um RTT.

**Porque continua a haver HELLOs para quem está em baixo:** se a rede entre dois nós vivos
falhar e voltar, nenhum dos dois reinicia, por isso nenhum se apresentaria. Com os HELLOs a
5 s, voltam a ouvir-se no máximo 5 s depois. É o que o BFD faz quando a sessão não está ativa.
Os 3 primeiros a 1 s seguem a ideia de desistir ao fim de cerca de 3 HELLOs, mas o nó abranda
em vez de parar.

**Fechar um nó.** `Ctrl+C` mata o nó na hora, sem avisar ninguém, como numa falha (pedido do
professor): os vizinhos dão por isso pela falta de vídeo ou pelo silêncio. A saída avisada faz-se
com `sair` + Enter no terminal do nó, ou com `kill <pid>` (SIGTERM).

Na saída avisada, o nó envia `CLOSE` fiável aos vizinhos vivos e aguarda os ACKs até
1 segundo. O recetor remove imediatamente o vizinho e o estado de fluxos associado, e deixa de
lhe mandar HELLOs; se o aviso se perder, o vizinho é dado como em baixo pelo silêncio.

## Gestão e encaminhamento de fluxos

Cada fluxo mantém o pai, os vizinhos que pediram o vídeo e um estado ativo/inativo. O primeiro
pedido local ou de um descendente propaga um `JOIN` fiável ao pai; os pedidos são renovados
sem fiabilidade de 2 em 2 segundos. Um destino que não renove durante 6 segundos é removido,
tal como se tivesse enviado `LEAVE`. Quando o último destino sai, o nó envia `LEAVE` ao pai.
O pai de cada fluxo é o pai escolhido para o servidor desse fluxo (secção seguinte).

Um nó só aceita `DATA` e `PING` do pai registado para o fluxo. Reencaminha-os para os destinos
ativos; `DATA` entrega o pacote RTP intacto ao player local. O servidor envia um `PING` por
fluxo ativo a cada 5 segundos. Cada nó acrescenta o seu ID e hora ao caminho e regista-o no
log, com o tempo de cada salto (ex: `PING 12 do vídeo (17, 0): 17 → 1 (+0.6 ms) → 14 (+0.5 ms)`).
O PING serve só para testes: nenhum nó o usa para escolher rotas.

O corpo do `DATA` é o pacote RTP tal como o `Servidor.py` o envia. O campo *timestamp* do RTP leva
a hora de envio em milissegundos (no código do professor eram segundos), para o cliente medir o
atraso. No CORE todos os nós partilham o relógio do computador, por isso a medida é fiável; numa
rede real não seria.

## Anúncios e escolha do pai (`ott/rotas.py`)

Corpo do `ANNOUNCE` (JSON, não fiável):

| Campo | O que é |
|---|---|
| `srv` | ID do nó do servidor |
| `ronda` | Número do anúncio. Começa em `int(time.time())` e o servidor soma 1 em cada anúncio |
| `videos` | Lista dos números dos vídeos do servidor (ex: `[0, 1]`) |
| `rtt` | Soma dos RTT mínimos, em ms, do servidor até quem envia o anúncio (0 no servidor) |
| `jitter` | Soma dos jitters, em ms, no mesmo caminho (0 no servidor; se faltar, conta como 0) |
| `perda` | Soma das perdas, em %, no mesmo caminho (0 no servidor; se faltar, conta como 0) |
| `saltos` | Número de ligações do servidor até quem envia o anúncio (0 no servidor) |

As quatro medidas vão em separado e cada nó aplica a fórmula do custo, que está numa só função
(`custo()` em `ott/rotas.py`). Para mudar os pesos não é preciso mudar o formato da mensagem:

```
custo = rtt (ms) + jitter (ms) + 20 × perda (%) + saltos
```

Como é uma soma, serve para uma ligação (`rtt_min + jitter + 20 × perda + 1`) e para o caminho
inteiro (a soma das ligações).

- O nó com `--servidor` envia um `ANNOUNCE` a todos os vizinhos de 2 em 2 s (±10% ao acaso).
- Quem recebe só aceita anúncios de vizinhos confirmados. Deita fora rondas mais antigas do que
  a última desse vizinho para esse servidor e anúncios com mais de 16 saltos.
- Guarda o último anúncio de cada vizinho, por servidor, já somadas as medidas da ligação a esse
  vizinho (`rtt_min`, `jitter` e `perda` do transporte, e 1 salto). Sem medições usa 100 ms de
  RTT e 0 de jitter e perda. O custo por esse vizinho é o custo anunciado mais o da ligação.
- Sem pai, fica com o vizinho de custo mais baixo entre os **viáveis** (regra abaixo).
- O anúncio de um vizinho vale 6 s (3 períodos). Se não for renovado, é apagado. Se não sobrar
  nenhum anúncio para um servidor, a rota para ele desaparece; quando o servidor deixa de
  anunciar, isto acontece em toda a rede.
- Se o pai cair (idle timeout, `CLOSE` ou reinício), o anúncio dele expirar ou ele enviar
  `WITHDRAW`, passa logo ao vizinho viável mais barato. Se não houver nenhum, envia `WITHDRAW`
  e fica sem pai até chegar um anúncio viável (em geral, de uma ronda mais nova do que a última
  que anunciou).
- **Falta de vídeo do pai.** Um nó com um fluxo ativo que fica **300 ms** sem `DATA` do pai
  (6 frames a 20 por segundo) passa ao vizinho viável mais barato, se houver. Não dá o pai
  como em baixo no transporte, só troca de pai; sem vizinho viável, fica com o pai e volta a
  tentar de 0,25 em 0,25 s. No arranque só vigia depois do primeiro `DATA`, para não disparar
  enquanto o servidor ainda não envia. O ciclo corre de 0,25 em 0,25 s, por isso a troca dá-se
  entre 0,3 e 0,55 s depois do último frame.
- **Pai novo que também não envia.** O vizinho escolhido pode receber o vídeo através do mesmo
  nó que falhou, que ainda ninguém deu como em baixo. Por isso, depois de uma troca num fluxo
  que já tinha vídeo, o pai novo tem **1 s** para enviar o primeiro `DATA`; se não enviar, o nó
  volta a procurar. O `JOIN` para o pai novo leva a marca `{"recuperar": true}`: se esse pai
  também não está a receber o vídeo, vigia o seu próprio pai da mesma forma e passa a marca no
  `JOIN` que manda para cima. Sem isto, o nó ficava preso a um pai sem vídeo até ele ser dado
  como em baixo (3 s). No arranque não há marca, por isso ninguém troca de pai enquanto o servidor
  ainda não envia vídeo.
- **Servidor mais perto (só no cliente, `--ver k`).** Os servidores têm os mesmos vídeos pela
  mesma ordem. O cliente pede o vídeo k ao servidor com rota mais barata entre os que o
  anunciam em `videos`. Na 1.ª escolha espera duas rondas (4 s) depois da 1.ª rota, para ouvir
  todos os servidores. Se esse servidor ficar sem rota (pai `None`), passa para o outro, se
  houver rota (`LEAVE` do fluxo antigo, `JOIN` do novo); se o primeiro voltar e for mais barato
  pela margem (15% e 2 unidades), volta para ele. O fluxo no cabeçalho continua a ser
  (servidor, vídeo): os outros nós não mudam. No log: `vídeo k: servidor S (custo X)`.
- **`WITHDRAW` (perda de rota).** Corpo `{"srv": S}`, fiável, enviado a todos os vizinhos por um
  nó que perdeu o pai para o servidor S e não tem nenhum vizinho viável. Quem o recebe do seu
  pai trata-o como a queda do pai (mesma função: outro vizinho viável ou, sem nenhum, também
  `WITHDRAW`). Quem o recebe de outro vizinho só apaga o anúncio desse vizinho para S. Assim, os
  nós a jusante não esperam os 6 s de validade dos anúncios para procurar outro caminho.
- Uma rota que fica vazia por `WITHDRAW` ou vizinho em baixo só é apagada 6 s depois do último
  anúncio que guardou, para a ronda e o custo que o nó anunciou continuarem a valer na regra
  anti-ciclo contra anúncios antigos que ainda andem pela rede.
- **Troca por um vizinho melhor**, com o pai vivo: só se o custo por esse vizinho for mais baixo
  em mais de **15%** e em mais de **2 unidades**, comparando anúncios **da mesma ronda** do pai,
  e só se o vizinho for viável. A margem evita trocas a toda a hora entre caminhos parecidos. Também
  corrige o arranque: um nó que ficou com um pai pior, por o primeiro anúncio ter vindo pelo
  caminho longo, passa ao melhor na ronda seguinte.
- **Regra anti-ciclo (viabilidade), em todas as escolhas de pai.** Um vizinho só pode ser pai se
  o anúncio dele for de uma ronda mais nova do que a última que este nó anunciou, ou da mesma
  ronda com um custo anunciado (a fórmula aplicada ao corpo) menor do que o menor custo que este nó
  anunciou nessa ronda. Quem recebe o fluxo através deste nó só conhece rondas que este nó já
  anunciou e anuncia sempre mais (soma pelo menos uma ligação e 1 salto), por isso nunca é
  viável: não se formam ciclos. É a condição de viabilidade do Babel (RFC 8966) e do EIGRP.
- Quando escolhe um pai ou recebe uma ronda nova do pai, anuncia a todos os vizinhos menos ao
  pai, com a ronda do pai e as suas próprias medidas acumuladas.
- Quando o pai de um servidor muda, chama `definir_pai` para cada vídeo desse servidor que está
  na tabela de fluxos ou foi pedido pelo player local (`--ver`): o `JOIN` sobe sozinho para o
  pai novo. Sem pai, chama `definir_pai(fluxo, None)`.
- No log: `[id] pai para o servidor S: V (custo X)`, com o motivo entre parênteses quando é uma
  troca (ex: `(melhor do que 2, custo 102.0)` ou `(vizinho 2 em baixo)`).

## Fiabilidade

- Um pacote com `FIAVEL` é confirmado com um `ACK` que leva **o pn desse pacote**. O ACK não é fiável e nunca é confirmado.
- Se não chegar ACK dentro do **RTO**, a mensagem é reenviada com **pn novo** e o **mesmo id da mensagem**.
  - Como o pn nunca se repete, cada ACK diz exatamente a que envio responde. O RTT medido não é ambíguo, que é o problema que o TCP contorna com o algoritmo de Karn. É isto que permite usar o RTT no custo das rotas.
  - Como o id da mensagem se mantém, o recetor deteta duplicados quando foi o ACK que se perdeu. Volta a enviar ACK, mas não entrega a mensagem outra vez. **Entrega exatamente uma vez.**
- RTO = `srtt + 4·rttvar` (RFC 6298), com mínimo de 0,2 s. Antes da 1.ª medição é 0,5 s. Duplica a cada tentativa até um máximo de 2 s.
- Para um **vizinho**, retransmite-se até haver ACK ou até o idle timeout o dar como em baixo (a mensagem é então cancelada). ACKs perdidos não chegam para dar um vizinho como morto: no CORE, com 20% de perda, isso deu um falso alarme.
- Para quem **não é vizinho** (ex: o bootstrapper), desiste-se ao fim de **5 tentativas**.
- O RTT é atualizado com médias móveis exponenciais (`srtt ← 7/8·srtt + 1/8·amostra`), como no TCP e no QUIC.

## Vizinhos vivos (limite de silêncio)

- Se não chega **nada** de um vizinho (HELLO, ACK, vídeo…) durante o **limite de silêncio**, o vizinho é dado como em baixo, as mensagens pendentes para ele são canceladas e é chamado `on_neighbor_down(id)`.
- Limite de silêncio = **3 × o intervalo que o vizinho anunciou no HELLO**, e nunca menos do que **3 × o RTO** para ele. Com o HELLO a 1 s, dá 3 s. Os tempos ficam combinados entre cada par de vizinhos, como no BFD (multiplicador 3, o valor habitual), e numa ligação lenta o limite estica sozinho, como a regra do QUIC para o idle timeout (RFC 9000).
- Com vídeo a passar, a falha deteta-se mais depressa pela falta de vídeo: 300 ms sem DATA do pai levam o nó a trocar de pai (ver "Anúncios e escolha do pai").
- Medidas por vizinho, usadas no custo das rotas, tiradas dos ACK dos HELLOs: `rtt_min` (mínimo das últimas 10 amostras de RTT), `jitter` (o `rttvar` do RFC 6298) e `perda` (fração dos últimos 20 HELLOs sem ACK em 1 s; mede a ida e a volta juntas).
- Quando um vizinho é dado como em baixo, a memória de duplicados dele é apagada: se voltar, é uma ligação nova.

## Interface do módulo (`ott/transporte.py`)

```python
t = Transporte(meu_id=0, on_message=cb, on_neighbor_down=cb2)
await t.start()                      # 0.0.0.0:5000
t.set_address(0, (ip_boot, 5000))    # conhecer o bootstrapper (não é vizinho)
t.send(0, Tipo.REGISTER, {}, fiavel=True)
t.set_id(3)                          # depois do REGISTER_ACK
t.add_neighbor(2, ("10.0.0.1", 5000))  # passa a ter limite de silêncio e medidas
t.set_intervalo(2, 1.0)              # intervalo de HELLO que o vizinho anunciou
t.send(2, Tipo.HELLO, {...}, sonda=True)  # pede ACK, sem retransmissão
t.send(2, Tipo.JOIN, fluxo=(1, 0), fiavel=True)
t.send(5, Tipo.DATA, rtp_bytes, fluxo=(1, 0))  # vídeo, sem fiabilidade
t.rtt(2)                             # RTT suavizado em segundos (ou None)
t.rtt_min(2) / t.jitter(2)           # segundos (ou None)
t.perda(2)                           # fração de 0 a 1 (ou None)
t.neighbors / t.remove_neighbor(2) / t.close()

def cb(origem, tipo, corpo, fluxo, addr): ...   # corpo: dict (controlo) ou bytes (DATA)
def cb2(vizinho_id): ...
```

Os callbacks são funções normais, não `async`, e não podem bloquear. Se precisares de esperar por alguma coisa, usa `asyncio.create_task(...)`.

O bootstrapper (`python3 -m bootstrapper <config.json>`) usa o IP de origem do `REGISTER` para
procurar o ID em `nodes` e constrói `vizinhos` a partir de `links`. Cada ligação tem de declarar
os IDs e IPs das duas pontas; os IPs têm de estar associados aos respetivos IDs em `nodes`.
Um IP não configurado ou uma mensagem `REGISTER` inválida é registado no log e não recebe
`REGISTER_ACK`.

## Parâmetros

| Parâmetro | Valor | Porquê |
|---|---|---|
| Porta | 5000 | Igual em todos os nós: no CORE cada nó tem o seu namespace |
| HELLO a um vizinho vivo | 1 s | BFD: intervalo combinado entre vizinhos. A deteção rápida, com vídeo a passar, é a falta de vídeo (0,3 s); o HELLO só tem de apanhar o resto |
| HELLO a quem não responde | 1 s nos 3 primeiros, depois 5 s | BFD: com a sessão em baixo continua a enviar, mais devagar. Uma ligação que volta é retomada em 5 s, no máximo |
| Limite de silêncio | 3 × intervalo anunciado (3 s), nunca abaixo de 3 × RTO | BFD (multiplicador 3) e a regra do QUIC para o idle timeout |
| RTO inicial / mín. / máx. | 0,5 / 0,2 / 2 s | O RTT no CORE é de dezenas de ms |
| Tentativas (só não vizinhos) | 5 | Com 20% de perda em cada sentido, a probabilidade de falhar as 5 é ≈ 0,6% (0,36⁵). Para vizinhos não há limite: decide o idle timeout |
| Período dos anúncios | 2 s, ±10% ao acaso | Os dois servidores não anunciam sempre no mesmo instante |
| Validade de um anúncio | 6 s (3 períodos) | Aguenta a perda de 2 anúncios seguidos |
| Limite de saltos de um anúncio | 16 | Rede de segurança contra ciclos, como no RIP |
| Margem para trocar de pai | mais de 15% e mais de 2 unidades | Não andar a saltar entre caminhos parecidos |
| Renovação do JOIN / validade de um destino | 2 s / 6 s | Como no PIM: o que não é renovado expira sozinho |
| Falta de vídeo do pai | 300 ms | 6 frames a 20 por segundo |
| Prazo de um pai novo para enviar | 1 s | Chega para o `JOIN` subir alguns saltos e o vídeo descer, mesmo com centenas de ms por ligação |
| PING | de 5 em 5 s | Só para testes |
| Portas locais do vídeo | 24000 + k (vídeo k, no servidor), 25000 (player) | Entre o nó e o `Servidor.py` / `Cliente.py`, na mesma máquina |

## Testar

- `python3 -m unittest -v`: testes automáticos em localhost (transporte com perda simulada de 30% e 40%, bootstrapper, rotas, vídeo e medições).
- No CORE: `sudo -E bash scripts/arrancar_cenario.sh 3` arranca um cenário inteiro, e `sudo -E bash scripts/testar_hello.sh` corre os 4 testes do HELLO. Tudo explicado em [`testes.md`](testes.md).

## O que seguimos

O professor pediu que disséssemos o que seguimos, em vez de justificar os valores só com testes nossos.

| No nosso protocolo | De onde vem a ideia |
|---|---|
| Um só socket UDP e fiabilidade feita pelo programa, só para o controlo | QUIC (RFC 9000) |
| Nº de pacote nunca reutilizado, nem nos reenvios; id da mensagem à parte, para detetar duplicados | QUIC (RFC 9000): *packet number* e *stream offset* |
| RTO calculado a partir do RTT (`srtt + 4·rttvar`), com backoff | TCP (RFC 6298) |
| Limite de silêncio nunca abaixo de 3 × o tempo de reenvio | Regra do QUIC para o *idle timeout* (RFC 9000) |
| Uma só mensagem para descobrir e vigiar os vizinhos, com a lista dos vizinhos ouvidos e o estado "a ouvir" | Hello do OSPF (RFC 2328) |
| Intervalo do HELLO dito no próprio pacote, em baixo ao fim de 3 intervalos, envio mais lento com a sessão em baixo | BFD (RFC 5880) |
| Anúncios com número de ronda e custo somado em cada salto | Vetor de distâncias com nº de sequência: DSDV e Babel (RFC 8966) |
| Nunca escolher como pai quem recebe através de nós (vizinho viável) | Condição de viabilidade do Babel e do EIGRP |
| Anúncios passados a todos os vizinhos, apagados se não forem renovados | Inundação do OSPF (RFC 2328): nº de sequência e idade máxima |
| JOIN salto a salto na direção do servidor, renovado periodicamente | Multicast nos routers: PIM (RFC 7761) |
| Um overlay que reage a falhas mais depressa do que a rede por baixo | *Resilient Overlay Networks* (Andersen e outros, 2001) |

## Glossário

Os termos técnicos deste documento, em palavras simples.

| Termo | O que quer dizer |
| --- | --- |
| **UDP** | Forma de enviar pacotes sem garantias: cada pacote vai sozinho e pode perder-se, chegar fora de ordem ou repetido. É rápido e simples. |
| **TCP** | O contrário do UDP: garante que tudo chega, e por ordem, mas obriga a uma ligação por destino e a esperas quando algo se perde. |
| **QUIC** | Protocolo moderno (o do HTTP/3) que dá as garantias do TCP por cima de UDP, dentro do próprio programa. Foi a inspiração do nosso. |
| **Overlay** | A rede "virtual" que os nossos programas formam por cima da rede real. Dois vizinhos no overlay não precisam de estar ligados por um cabo. |
| **Bootstrapper** | Programa que só recebe os nós novos e lhes diz o ID e os vizinhos. Não entra nas rotas. |
| **Cabeçalho** | Os primeiros bytes de cada pacote: quem envia, para quem, que tipo de mensagem, etc. |
| **ACK** | Mensagem curta de "recebi o pacote nº X" (de *acknowledgement*, confirmação). |
| **Mensagem fiável** | Mensagem que o transporte garante que chega: espera pelo ACK e, se não vier, volta a enviar. |
| **Retransmissão** | Voltar a enviar uma mensagem cujo ACK não chegou. |
| **Nº de pacote (pn)** | Número único de cada pacote enviado. Nunca se repete, nem quando se reenvia. |
| **Id da mensagem** | Número da mensagem. Fica igual nos reenvios, para o recetor reconhecer uma repetida. |
| **Duplicado** | A mesma mensagem recebida duas vezes. Acontece quando a mensagem chegou mas o ACK se perdeu, e o emissor reenviou. |
| **RTT** | Tempo de ida e volta (*round-trip time*): desde que se envia um pacote até chegar o ACK dele. |
| **srtt / rttvar** | A média suavizada do RTT / quanto o RTT costuma variar. |
| **RTO** | Quanto tempo se espera pelo ACK antes de reenviar (*retransmission timeout*). RTO = srtt + 4 × rttvar, nunca menos de 0,2 s nem mais de 2 s. |
| **Backoff exponencial** | Duplicar a espera a cada tentativa falhada (0,2 → 0,4 → 0,8 s…), para não encher a rede de reenvios. |
| **Sinal de vida (keepalive)** | Mensagem só para dizer "estou vivo". No nosso protocolo é o HELLO periódico. |
| **Idle timeout / limite de silêncio** | Tempo sem receber nada de um vizinho ao fim do qual ele é dado como em baixo (3 × o intervalo de HELLO que ele anunciou, 3 s). |
| **Sonda** | Mensagem que pede ACK mas não é reenviada (o HELLO): serve para medir o RTT e a perda. |
| **Número de arranque** | Número sorteado quando um nó arranca. Se mudar nos HELLOs, o nó reiniciou. |
| **OSPF** | Protocolo de encaminhamento dos routers; o seu Hello periódico, com a lista dos vizinhos ouvidos, inspirou o nosso HELLO. |
| **BFD** | Protocolo que os routers usam para detetar depressa que um vizinho deixou de responder: intervalo combinado entre os dois e "em baixo" ao fim de 3 em falta. |
| **Falso "em baixo"** | Dar como morto um vizinho que está vivo, porque se perderam todos os pacotes dele durante o idle timeout. |
| **ISN** | O número com que o contador de pacotes começa (*initial sequence number*). É aleatório, para um nó reiniciado não repetir números antigos. |
| **Fluxo / stream** | Um vídeo de um servidor, identificado pelo par (servidor, nº do vídeo). |
| **CID** | No QUIC, o número que identifica uma ligação (*connection ID*). O nosso equivalente é o ID do nó. |
| **Ack-eliciting** | Pacote que pede ACK. |
| **struct, `!`** | Módulo do Python que converte os campos em bytes. O `!` quer dizer "ordem de rede": todas as máquinas leem os números da mesma maneira. |
| **JSON** | Formato de texto para dados, ex: `{"id": 3}`. |
| **asyncio** | Forma de o Python tratar várias coisas "ao mesmo tempo" numa só thread, com temporizadores. |
| **Callback** | Função nossa que o transporte chama quando acontece algo (chegou uma mensagem, um vizinho caiu). |
| **Inundação controlada** | Enviar uma mensagem a todos os vizinhos, que a passam aos deles, deitando fora as repetidas. |
| **JOIN / LEAVE** | Pedido para receber um fluxo, enviado ao pai e renovado periodicamente / aviso de que já não se quer. |
| **Anúncio, ronda** | Mensagem periódica de cada servidor que espalha o custo até ele; a ronda é o número de cada envio. |
| **Pai** | O vizinho de quem um nó recebe os vídeos de um servidor. |
| **Vizinho viável** | Vizinho que pode ser pai sem criar um ciclo (regra anti-ciclo). |
| **Margem** | Diferença mínima de custo para valer a pena trocar de pai. |
| **WITHDRAW** | Aviso de "perdi a rota para este servidor". |
| **RIP, DSDV, Babel, EIGRP, PIM** | Protocolos de encaminhamento reais em que nos inspirámos para algumas regras das rotas. |
| **RFC** | Documento que especifica um protocolo da Internet (ex: o RFC 9000 é o QUIC). |
| **CORE** | O emulador de redes onde corremos os testes. |
