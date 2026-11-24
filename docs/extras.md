# TP2 ESR — Extras

Atualizado a 23/11/2026 · Grupo: George, Inês, Pedro

Um **extra** é algo que o guião não pede. Este documento diz o estado de cada um: feito, por decidir ou tirado. Como funcionam os que estão feitos está no [`protocolo.md`](protocolo.md); aqui só fica o resumo.

**A história que os extras contam:** a rede recupera sozinha quando algo falha ou piora, e conseguimos medi-lo.

**Regras do professor que todos respeitam:**

- O bootstrapper só dá a cada nó novo o seu número e os vizinhos. Não faz mais nada.
- Um nó só conhece os seus vizinhos, não a rede inteira nem a árvore por onde o vídeo passa. A única exceção autorizada é o "avô" do pedido de socorro (mais abaixo).

Os números entre parênteses são os da lista original, de 3/11.

---

## Feitos

| Extra | O que faz | Onde |
|---|---|---|
| Reparação local (1) | Quando o pai cai, o nó passa logo ao vizinho viável mais barato, sem esperar pelo anúncio seguinte | `ott/rotas.py` |
| Aviso de perda de rota (2) | Um nó sem pai e sem vizinho viável manda WITHDRAW, para quem recebia dele procurar outro caminho | `ott/rotas.py` |
| Detetar a falha pela falta de vídeo (5) | 300 ms sem vídeo do pai fazem trocar de pai. No CORE, o vídeo parou 0,35 a 0,55 s, contra cerca de 3 s pelo silêncio | `ott/__main__.py` |
| Mudar de caminho quando o atual piora (4) | Troca por um vizinho melhor só com mais de 15% e 2 unidades de diferença (a margem) | `ott/rotas.py` |
| Custo com quatro medidas (6) | RTT, variação do atraso, perda e saltos. **A fórmula vai mudar** para uma métrica ao estilo ETX/ETT (decidido a 22/11): o peso 20 da perda não tinha fundamento | `custo()` em `ott/rotas.py` |
| Anunciar de novo quando a rota muda (27) | Os nós a seguir ficam a conhecer o melhor custo, e não o do primeiro anúncio a chegar | `ott/rotas.py` |
| Rotas que expiram (28) | O anúncio de um vizinho vale 6 s; um servidor que morre desaparece das rotas de toda a rede | `ott/rotas.py` |
| Anúncios sem confirmação (29) | Um anúncio perdido é substituído pelo seguinte, 2 s depois | `ott/rotas.py` |
| Limite de saltos (30) | Anúncios com mais de 16 saltos são deitados fora | `ott/rotas.py` |
| Período com variação ao acaso (31) | 2 s ± 10%, para os dois servidores não anunciarem sempre no mesmo instante | `ott/rotas.py` |
| Lista de vídeos nos anúncios (parte do 7) | O anúncio leva os números dos vídeos do servidor | `ott/rotas.py` |
| Ir ao servidor mais perto (parte do 7) | O cliente pede "o vídeo k" e fica com o servidor mais barato | `ott/__main__.py` (`--ver k`) |
| Troca de servidor (8) | Se o servidor escolhido fica sem rota, o cliente pede o mesmo vídeo ao outro. **Problema:** congela o vídeo cerca de 2 s, porque o nº do frame está desfasado entre os servidores (o Pedro está a corrigir) | `ott/__main__.py` |
| Renovação do pedido de vídeo | O JOIN é repetido de 2 em 2 s; um destino sem renovação durante 6 s é largado | `ott/__main__.py` |
| Medições no cliente (21) | A hora de envio vai no timestamp do RTP, em ms; o medidor calcula atraso, jitter, perda e cortes | `player/Medidor.py`, `scripts/analisar_medicoes.py` |
| PING com a hora em cada salto (23) | Mostra o caminho do vídeo e o tempo de cada salto. Só para testes | `ott/__main__.py` |
| Logs organizados e desenho da árvore (22) | O painel e o `scripts/arvore.py` leem os logs de todos os nós e desenham a árvore de cada vídeo, em texto e numa figura SVG com as posições do CORE, também num instante passado (`--hora`). Nenhum nó fica a conhecer a árvore: quem a junta é uma ferramenta de teste | `scripts/logs_ott.py`, `scripts/painel.py`, `scripts/arvore.py` |
| Comparação com a solução sem overlay (24) | Com 3 clientes, o servidor envia 35% menos com overlay | [`resultados-medicoes.md`](resultados-medicoes.md) |

## Por decidir (reunião dos extras)

- **Pedido de socorro ao "avô" (33).** *Autorizado pelo professor.* Resolve o caso em que um nó perde todos os vizinhos de uma vez. O pedido faz-se **antes** da falha, porque depois já não há a quem perguntar:
  1. Um nó em risco pede ajuda ao pai.
  2. O pai responde com o número e o endereço do seu próprio pai: o **avô** do nó.
  3. Se o pai mudar de pai, avisa o nó do avô novo.
  4. Se o nó perder todos os vizinhos, apresenta-se ao avô. Passam a ser vizinhos normais, e o pedido do vídeo segue por aí.
  5. Se nem isso resultar, o nó fica isolado e vai tentando de tempos a tempos o avô e os vizinhos originais.

  *Regras, para a exceção ser a menor possível:* é a única situação em que um nó conhece alguém além dos vizinhos; o pai só diz quem é o avô a quem lho pede e nunca o espalha; o nó só guarda o avô enquanto está em risco; quando se liga ao avô, este passa a vizinho normal e a exceção acaba; serve para refazer uma ligação, não para calcular rotas.

  *"Estar em risco"* = ter só um vizinho vivo (a alternativa, "não ter reserva válida", acontece a muitos nós e a exceção passava a ser a regra).

  *Custo:* médio a alto. Obriga a aceitar apresentações de quem não está na lista do bootstrapper. Onde se vê: nos cenários 1 e 2, onde os clientes têm um só vizinho; no cenário 3 quase não acontece (cada cliente tem 2 vizinhos).
- **Medir a perda pelos números em falta.** Cada nó numera os pacotes que envia a cada vizinho, e o vizinho conta os que faltam. Mede só o sentido do vídeo e usa todos os pacotes, não só os HELLOs. Ajudava no problema das rotas que trocam de pai com 1 a 2% de perda. *Custo:* médio.
- **Leitor do nosso protocolo para o Wireshark (25).** Mostra os campos do nosso cabeçalho pelo nome. Dá prints claros para a especificação do protocolo. *Custo:* baixo a médio.
- **Comparar o overlay com o OSPF dos routers.** Desligar um router e ver quem reage primeiro: a rede real ou o overlay. Boa discussão crítica (*Resilient Overlay Networks*). Não muda o código; é uma sessão de testes.
- **Ligação com largura de banda limitada.** Mostra a cópia única com números: com overlay, uma ligação estreita aguenta vários clientes. Não muda o código.

## Tirados

- **Troca de caminho sem cortes (3).** O corte que evitava são milissegundos no CORE (um ou dois frames), e obrigava a tratar frames repetidos em todos os nós.
- **Partir os frames em pedaços (15).** Perder um pedaço estraga o frame na mesma, por isso não resolve a perda (ver [`frames.md`](frames.md)).
- **Buffer no player (16).** Custava 200 ms de atraso sempre, e o ganho só aparecia com muita variação do atraso.
- **Esconder frames perdidos (17).** O player já o faz: a imagem só muda quando chega um frame novo.

---

## Glossário

| Termo | O que quer dizer |
|---|---|
| **Overlay** | A rede "virtual" que os nossos programas formam por cima da rede real. |
| **Nó, vizinho** | Um dos nossos programas; um nó com quem fala diretamente no overlay. |
| **Bootstrapper** | Programa que só diz a cada nó novo o seu número e os vizinhos. |
| **Anúncio, ronda** | Mensagem periódica de cada servidor que espalha o custo até ele; a ronda é o número de cada envio. |
| **Custo** | Número que resume a qualidade de um caminho. Quanto mais baixo, melhor. |
| **Pai / avô** | O pai é o vizinho de quem um nó recebe o vídeo. O avô é o pai do pai. |
| **Vizinho viável** | Vizinho que pode ser pai sem criar um ciclo. |
| **Margem** | Diferença mínima de custo para valer a pena trocar de pai. |
| **JOIN / LEAVE / WITHDRAW** | Pedir um vídeo / desistir dele / avisar que se perdeu a rota para um servidor. |
| **PING** | Mensagem de teste que segue o caminho do vídeo e regista por onde passou. |
| **ETX / ETT** | Métricas de custo de redes sem fios: o número esperado de envios para um pacote passar (ETX) e o tempo que isso leva (ETT). |
| **Frame** | Uma imagem do vídeo. O vídeo de teste tem 20 por segundo. |
| **Wireshark** | Programa que mostra os pacotes que passam numa rede. |
| **OSPF** | O protocolo com que os routers do CORE escolhem os caminhos na rede real. |
| **CORE** | O emulador de redes onde corremos os testes. |
