# Protocolo OTT — transporte (Etapa 1)

Protocolo próprio sobre UDP, inspirado no QUIC (decisões D2, D3 e D6 no doc "TP2 ESR — Plano e decisões").
Cada nó usa **um só socket UDP**, na porta **5000**, para todos os vizinhos e para o bootstrapper.
Código: `ott/protocolo.py` (formato) e `ott/transporte.py` (comportamento).

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
| nº pacote (pn) | 4 B | Contador do nó, **nunca reutilizado**, nem nas retransmissões | Packet Number |
| fluxo: servidor | 2 B | ID do servidor de origem do fluxo. `0` = controlo | Stream ID |
| fluxo: stream | 1 B | Qual dos vídeos desse servidor | Stream ID |
| id da mensagem | 4 B | **Só em pacotes fiáveis.** Identifica a mensagem: é o pn do 1.º envio e mantém-se nas retransmissões | stream offset |

**Corpo:** JSON (UTF-8) nas mensagens de controlo, bytes crus no `DATA` (o pacote RTP do ProgEx, que já traz o nº de sequência e o timestamp).

**Fora do cabeçalho, de propósito:** o tamanho (o UDP já entrega o datagrama inteiro), o checksum (o UDP já tem) e o nº de sequência do vídeo (já vem no RTP).

## Tipos de mensagem

| Código | Tipo | Fiável | Corpo | Quem trata |
|---|---|---|---|---|
| 0x01 | REGISTER | sim | `{}` | Inês |
| 0x02 | REGISTER_ACK | sim | `{"id": 3, "vizinhos": [{"id": 2, "ip": "10.0.0.1"}, …]}` (proposta) | Inês |
| 0x03 | HELLO | sim | a definir pela Inês | Inês |
| 0x04 | HELLO_ACK | sim | a definir pela Inês | Inês |
| 0x05 | KEEPALIVE | pede ACK, não é retransmitido | vazio | transporte |
| 0x06 | ACK | não | pn confirmado, 4 B binário | transporte |
| 0x07 | CLOSE | sim | saída avisada (por implementar) | — |
| 0x10–0x1F | reservados para as rotas (Etapa 2) | | | |
| 0x20 | DATA | não | bytes do vídeo | streaming |

## Fiabilidade

- Um pacote com `FIAVEL` é confirmado com um `ACK` que leva **o pn desse pacote**. O ACK não é fiável e nunca é confirmado.
- Se não chegar ACK dentro do **RTO**, a mensagem é reenviada com **pn novo** e o **mesmo id da mensagem**.
  - Como o pn nunca se repete, cada ACK diz exatamente a que envio responde. O RTT medido não é ambíguo, que é o problema que o TCP contorna com o algoritmo de Karn. É isto que permite usar o RTT como métrica na Etapa 2.
  - Como o id da mensagem se mantém, o recetor deteta duplicados quando foi o ACK que se perdeu. Volta a enviar ACK, mas não entrega a mensagem outra vez. **Entrega exatamente uma vez.**
- RTO = `srtt + 4·rttvar` (RFC 6298), com mínimo de 0,2 s. Antes da 1.ª medição é 0,5 s. Duplica a cada tentativa até um máximo de 2 s.
- Ao fim de **5 tentativas** sem ACK, o destino é dado como em baixo.
- O RTT é atualizado com médias móveis exponenciais (`srtt ← 7/8·srtt + 1/8·amostra`), como no TCP e no QUIC.

## Vizinhos vivos (keepalive e idle timeout)

- Se um nó não enviou nada a um vizinho no último **1 s**, envia-lhe um `KEEPALIVE`. Assim só há keepalive quando a ligação está parada.
- O KEEPALIVE pede ACK, para medir o RTT mesmo sem tráfego, mas não é retransmitido: o seguinte faz esse papel.
- Se não chega **nada** de um vizinho durante **3,5 s** (cerca de 3 keepalives perdidos), o vizinho é dado como em baixo, as mensagens pendentes para ele são canceladas e é chamado `on_neighbor_down(id)`.

## Interface do módulo (`ott/transporte.py`)

```python
t = Transporte(meu_id=0, on_message=cb, on_neighbor_down=cb2)
await t.start()                      # 0.0.0.0:5000
t.set_address(0, (ip_boot, 5000))    # conhecer o bootstrapper (não é vizinho)
t.send(0, Tipo.REGISTER, {}, fiavel=True)
t.set_id(3)                          # depois do REGISTER_ACK
t.add_neighbor(2, ("10.0.0.1", 5000))  # passa a ter keepalive e idle timeout
t.send(2, Tipo.HELLO, {...}, fiavel=True)
t.send(5, Tipo.DATA, rtp_bytes, fluxo=(1, 0))  # vídeo, sem fiabilidade
t.rtt(2)                             # RTT suavizado em segundos (ou None)
t.neighbors / t.remove_neighbor(2) / t.close()

def cb(origem, tipo, corpo, fluxo, addr): ...   # corpo: dict (controlo) ou bytes (DATA)
def cb2(vizinho_id): ...
```

Os callbacks são funções normais, não `async`, e não podem bloquear. Se precisares de esperar por alguma coisa, usa `asyncio.create_task(...)`.

## Parâmetros

| Parâmetro | Valor | Porquê |
|---|---|---|
| Porta | 5000 | Igual em todos os nós: no CORE cada nó tem o seu namespace |
| Keepalive | 1 s | Deteção rápida sem gastar quase nada de rede |
| Idle timeout | 3,5 s | Tolera 3 keepalives perdidos seguidos |
| RTO inicial / mín. / máx. | 0,5 / 0,2 / 2 s | O RTT no CORE é de dezenas de ms |
| Tentativas | 5 | Com 20% de perda em cada sentido, a probabilidade de falhar as 5 é ≈ 0,6% (0,36⁵) |

## Testar

- `python3 -m unittest -v`: testes em localhost, com perda simulada de 30%.
- No CORE: `python3 -m ott.demo_transporte --id 2 1=10.0.0.10 3=10.0.1.20`. Mete perda num link e mata um nó (instruções no próprio ficheiro).
