# TP2 ESR — Extras (lista revista)

Atualizado a 14/11/2026 · Grupo: George, Inês, Pedro

Um **extra** é algo que o guião não pede. Este documento explica cada extra que temos em cima da mesa, o que obriga a mudar, quanto custa e se vale a pena. Lê-se sozinho. Substitui a lista de 3/11.

**Como usar:** cada extra tem uma caixa para o grupo marcar. A recomendação é só uma proposta; a decisão é nossa e, no fim, do professor. Os números entre parênteses são os da lista de 3/11, para quem a leu.

**Regras que todos os extras respeitam** (são do professor):

- O bootstrapper, o programa que recebe os nós novos, só lhes dá o número e os vizinhos. Não faz mais nada.
- Um nó só conhece os seus vizinhos. Não conhece a rede inteira nem a árvore por onde o vídeo passa. A única exceção proposta é o "avô" do pedido de socorro, explicada mais abaixo, e precisa de autorização.

**Porque é que a lista encolheu.** Faltam cerca de seis semanas e ainda não há rotas nem vídeo a passar pelo overlay. O relatório tem no máximo 12 páginas, e cada extra só conta se estiver explicado e testado, com prints. Vale mais ter poucos extras que contam uma história do que muitos a meio. A história proposta é: **a rede recupera sozinha quando algo falha ou piora, e conseguimos medi-lo.**

---

## Resumo

| Extra | Recomendação |
|---|---|
| Reparação local (1) | Entra com o básico |
| Aviso de perda de rota (2) | Entra com o básico |
| Mudar de caminho quando o atual piora (4) | Entra com o básico |
| Anunciar de novo quando aparece um caminho melhor (27) | Entra com o básico |
| Rotas que expiram (28) | Entra com o básico |
| Anúncios sem confirmação (29) | Entra com o básico |
| Limite de saltos (30) | Entra com o básico |
| Período dos anúncios com variação ao acaso (31) | Entra com o básico |
| Custo com quatro medidas (6) | Entra com o básico (já decidido) |
| Lista de vídeos nos anúncios (parte do 7) | Entra com o básico |
| Renovação do pedido de vídeo (novo) | Já implementada |
| Medições no cliente (21) | Entra com o básico |
| Mensagem de teste com a hora em cada salto (23) | Já implementada |
| Comparação com a solução sem overlay (24) | Entra com o básico |
| Detetar a falha pela falta de vídeo (5) | Logo a seguir |
| Pedido de socorro, com o "avô" (33) | Só se sobrar tempo, e com autorização do professor |
| Logs organizados e desenho da árvore (22) | Só se sobrar tempo |
| Leitor do nosso protocolo para o Wireshark (25) | Só se sobrar tempo |
| Comparar o overlay com o encaminhamento da rede real (a "opção B" do OSPF) | Só se sobrar tempo |
| Medir a perda pelos números em falta (novo) | Só se sobrar tempo |
| Ir ao servidor mais perto (parte do 7) | Depende da resposta do professor |
| Troca de caminho sem cortes (3) | Tirar |
| Partir os frames em pedaços (15) | Tirar |
| Buffer no player (16) | Tirar |
| Esconder frames perdidos (17) | Tirar: o player já o faz |
| Troca de servidor (8) | Tirar |

---

## Antes de ler: como funcionam as rotas

Os extras mexem quase todos nas rotas, por isso convém ter a ideia presente:

- Cada servidor envia de 2 em 2 segundos um **anúncio** ("estou aqui, com estes vídeos"), que passa de vizinho em vizinho por toda a rede. Cada envio do servidor tem um número, a **ronda**.
- Cada nó soma ao anúncio o **custo** da ligação por onde ele chegou. O custo junta o tempo de ida e volta, a variação do atraso, a perda e o número de saltos. Quanto mais baixo, melhor.
- Cada nó fica com o vizinho que lhe dá o custo mais baixo até ao servidor: é o seu **pai**, de quem vai receber o vídeo.
- Um cliente que quer um vídeo manda um pedido (**JOIN**) ao pai, que o passa ao pai dele, até ao servidor. O vídeo desce pelo caminho inverso. Quem já não quer o vídeo manda um **LEAVE**.

---

## 1. Entram com o básico

Fazem parte do desenho das rotas. Decidi-los depois obrigava a refazer código.

### Recuperação de rotas

- [ ] **Reparação local (1).** Quando o pai cai, o nó troca logo para um vizinho de reserva e manda-lhe o pedido do vídeo, sem esperar pelo anúncio seguinte.
  - *O que muda:* cada nó guarda o último anúncio de **cada** vizinho, e não só o do melhor.
  - *Como se evita um ciclo:* o perigo é escolher como reserva um nó que recebe o vídeo através de nós, e o vídeo ficar a andar em círculo. O nó não conhece a árvore, mas há uma regra que chega: **só serve de reserva um vizinho que, na mesma ronda, anunciou um custo menor do que o nosso** (ou que já vai numa ronda mais nova do que a nossa). Quem recebe através de nós anuncia sempre mais do que nós, porque soma pelo menos uma ligação. A regra vem de protocolos reais (o Babel e o EIGRP).
  - *Limite:* nem sempre há reserva válida. Nesse caso o nó espera pelo anúncio seguinte, no máximo 2 segundos.
  - *Onde se vê:* onde há caminhos alternativos. No cenário 2, entre os três nós intermédios; no cenário 3, com 2 vizinhos por cliente. No cenário 1 não há alternativa.
  - *Custo:* baixo a médio.

- [ ] **Aviso de perda de rota (2).** Um nó que fica sem pai e sem reserva avisa logo os vizinhos (mensagem WITHDRAW), para quem recebia dele procurar outro caminho em vez de ficar à espera.
  - *O que muda:* um tipo de mensagem novo.
  - *Custo:* baixo.

- [ ] **Mudar de caminho quando o atual piora (4).** Se o custo do caminho atual sobe (mais atraso ou mais perda) e outro vizinho passa a ser melhor, o nó troca. Mas só troca se a diferença passar uma **margem** (proposta: mais de 15%), para não andar a saltar entre dois caminhos parecidos.
  - *Nota:* o guião já pede rotas que se atualizam com as condições da rede. O extra é a margem.
  - *Custo:* baixo.

- [ ] **Rotas que expiram (28).** O anúncio de um vizinho só vale 6 segundos. Se não chegar outro, é apagado. Cobre o caso de um servidor que morre: os anúncios param e as rotas para ele desaparecem de toda a rede.
  - *Custo:* baixo.

### Anúncios

- [ ] **Anunciar de novo quando aparece um caminho melhor (27).** O primeiro anúncio de uma ronda a chegar a um nó nem sempre veio pelo melhor caminho. Se o nó só passasse esse, os nós a seguir ficavam a conhecer um custo pior do que o real. Por isso, quando na mesma ronda aparece um caminho melhor e o nó troca, anuncia outra vez.
  - *Custo:* baixo. Sai de graça se a regra for "anuncio sempre que a minha rota muda".

- [ ] **Anúncios sem confirmação (29).** Os anúncios são enviados uma vez, sem confirmação nem reenvio. Um anúncio perdido é substituído pelo seguinte, 2 segundos depois. Os pedidos de vídeo e os avisos continuam a ser confirmados.
  - *Custo:* nenhum. Poupa tráfego.

- [ ] **Limite de saltos (30).** Um anúncio com mais de 16 saltos é deitado fora. É uma rede de segurança: se um erro nosso criar um ciclo, o anúncio não fica a andar para sempre.
  - *Custo:* nenhum. O número de saltos já vai no anúncio, por ser uma das medidas.

- [ ] **Período com variação ao acaso (31).** Cada servidor anuncia de 2 em 2 segundos, mais ou menos 10%. Evita que os dois servidores anunciem sempre no mesmo instante.
  - *Custo:* uma linha de código.

### Custo e vídeos

- [x] **Custo com quatro medidas (6).** Já decidido em grupo: tempo de ida e volta, variação do atraso, perda e saltos. Usar medidas da rede é pedido pelo guião; o extra é juntarmos quatro.
  - *Por decidir, como as juntar.* A proposta é uma soma: tempo de ida e volta + variação do atraso + 20 por cada 1% de perda + 1 por salto. A alternativa era excluir primeiro as ligações com perda acima de um limite e só depois olhar para o tempo, mas uma ligação com a perda a oscilar à volta do limite faria a rota saltar a toda a hora.
  - *Custo:* baixo. O tempo de ida e volta e a variação já são medidos.

- [ ] **Lista de vídeos nos anúncios (parte do 7).** O anúncio leva o nome de cada vídeo do servidor. O cliente escolhe pelo nome.
  - *Nota:* com 2 vídeos por servidor, o cliente tem de saber o que existe. Na prática isto é preciso de qualquer forma.
  - *Custo:* baixo.

- [x] **Renovação do pedido de vídeo (novo).** *Já implementada (11/11).* Além do pedido inicial, cada nó repete o pedido ao pai de 2 em 2 segundos. Se o pai ficar 6 segundos sem renovação, deixa de enviar.
  - *Porquê:* sem isto, uma rota fica ativa até alguém a desligar, e isso falha em dois casos. Primeiro, o nosso transporte não garante a ordem das mensagens: uma desistência reenviada pode chegar depois de um pedido novo, e o pai deixa de enviar para sempre. Segundo, um nó que reinicia depressa não é dado como em baixo, e o pai continua a mandar-lhe vídeo. Com a renovação, os dois casos corrigem-se sozinhos em segundos. É como o multicast funciona nos routers.
  - *Nota:* substitui a proposta de 3/11, em que as rotas ficavam ativas sem renovação.
  - *Custo:* baixo.

### Medir e provar

- [ ] **Medições no cliente (21).** Cada pacote de vídeo leva a hora a que saiu do servidor. O cliente calcula quanto tempo demorou e quanto esse tempo varia. O player já conta os frames recebidos e os que faltam.
  - *Porquê:* são os números que o relatório precisa. Sem eles não conseguimos dizer quanto tempo o vídeo esteve parado numa falha.
  - *O que muda:* 8 bytes a mais em cada pacote de vídeo. A hora que já vem dentro do pacote do código do professor não serve, porque está em segundos inteiros.
  - *Nota:* no CORE todos os nós partilham o relógio do computador, por isso a medida é fiável. Numa rede real não seria.
  - *Custo:* baixo.

- [x] **Mensagem de teste com a hora em cada salto (23).** *Já implementada (11/11).* O guião já pede uma mensagem de teste (PING) que segue as rotas e regista por onde passou. O extra é cada nó juntar também a hora, para se ver o tempo de cada salto.
  - *Nota:* serve só para testes e prints. Nenhum nó a usa para escolher rotas.
  - *Custo:* baixo.

- [ ] **Comparação com a solução sem overlay (24).** Pôr o servidor a enviar uma cópia do vídeo diretamente a cada cliente, sem overlay, e comparar o tráfego à saída do servidor com o do overlay.
  - *Porquê:* prova com números aquilo para que o trabalho existe. É talvez o teste mais importante do relatório.
  - *Custo:* baixo a médio. O programa de vídeo do professor já aceita o endereço de destino.

---

## 2. Logo a seguir

- [ ] **Detetar a falha pela falta de vídeo (5).** Com vídeo a passar, chega um frame a cada 50 ms. Se um nó ficar 300 ms sem receber vídeo do pai (6 frames seguidos), começa logo a reparação, sem esperar pelos 3 segundos de silêncio.
  - *Ganho:* o tempo sem vídeo numa falha passa de cerca de 3 s para cerca de 0,3 s. Em 3 s perdem-se 60 frames; em 0,3 s, 6.
  - *Porque sobe na lista:* é o extra com mais efeito visível por menos trabalho. E responde ao pedido do professor de um mecanismo mais adaptativo: o tempo de deteção passa a depender do ritmo do que está a passar.
  - *Risco:* com muita perda, podem falhar 6 frames seguidos sem haver falha. O efeito é só uma troca para a reserva.
  - *Depende de:* a reparação local.
  - *Custo:* baixo.

---

## 3. Só se sobrar tempo

- [ ] **Pedido de socorro, com o "avô" (33).** Resolve o caso em que um nó perde todos os vizinhos de uma vez. O pedido é feito **antes** da falha, porque depois já não há a quem perguntar.
  1. Um nó em risco pede ajuda ao pai.
  2. O pai responde com o número e o endereço do seu próprio pai: o **avô** do nó.
  3. Se o pai mudar de pai, avisa o nó do avô novo.
  4. Se o nó perder todos os vizinhos, apresenta-se ao avô. Passam a ser vizinhos normais, e o pedido do vídeo segue por aí.
  5. Se nem isso resultar, o nó fica isolado e vai tentando de tempos a tempos o avô e os vizinhos originais.
  - *Onde se vê:* nos cenários 1 e 2, cada cliente tem um só vizinho. No cenário 2, se cair o nó intermédio a que um cliente está ligado, o cliente liga-se ao nó seguinte no caminho para o servidor, e o vídeo volta.
  - *Regras do avô*, para a exceção ser a menor possível:
    - É a única situação em que um nó conhece alguém além dos vizinhos.
    - O pai só diz quem é o avô a quem lho pede, e nunca o espalha.
    - O nó só guarda o avô enquanto está em risco.
    - Quando o nó se liga ao avô, este passa a vizinho normal e a exceção acaba.
    - Serve para refazer uma ligação, não para calcular rotas.
  - *Por decidir, o que é "estar em risco":* a proposta é **ter só um vizinho vivo**. A alternativa era "não ter reserva válida", mas isso acontece a muitos nós, mesmo com vários vizinhos vivos, e a exceção passava a ser a regra.
  - *Porque fica para o fim:*
    - Fura a regra de o nó só conhecer os vizinhos. **Precisa da autorização do professor.**
    - Obriga os nós a aceitar apresentações de quem não está na lista dada pelo bootstrapper, o que pede regras novas: quando recusar, e o que fazer à ligação de emergência quando o vizinho original voltar.
    - No cenário 3, cada cliente tem 2 vizinhos, e o caso quase não acontece.
  - *Custo:* médio a alto.

- [ ] **Logs organizados e desenho da árvore (22).** Cada nó grava cada acontecimento (vizinho ativo, pai escolhido, pedido recebido…) numa linha de um ficheiro, num formato fácil de ler por um programa. Um script, que corre **fora dos nós**, junta os ficheiros de todos e desenha o overlay e a árvore por onde o vídeo passa.
  - *Nota:* nenhum nó fica a conhecer a árvore. Quem a desenha é uma ferramenta de teste.
  - *Ganho:* figuras para o relatório e para a defesa.
  - *Custo:* baixo a médio.

- [ ] **Leitor do nosso protocolo para o Wireshark (25).** Um pequeno script que ensina o Wireshark (o programa de análise de tráfego) a mostrar os campos do nosso cabeçalho pelo nome.
  - *Ganho:* prints claros para a secção do relatório que especifica o protocolo.
  - *Custo:* baixo a médio.

- [ ] **Comparar o overlay com o encaminhamento da rede real.** Os routers do CORE têm o seu próprio protocolo de encaminhamento (o OSPF), que desvia o tráfego quando um router cai. O teste é desligar um router e ver quem reage primeiro: a rede real, a desviar os pacotes, ou o overlay, a trocar de vizinho.
  - *Ganho:* uma boa discussão crítica. Há um trabalho conhecido (*Resilient Overlay Networks*) que mostra que um overlay pode reagir mais depressa do que a rede por baixo.
  - *Depende de:* a topologia grande, com routers alternativos. Não muda nada no código.
  - *Custo:* uma sessão de testes.

- [ ] **Medir a perda pelos números em falta (novo).** Na proposta base, a perda mede-se contando os sinais de vida que ficam sem confirmação. É simples, mas mede a ida e a volta juntas e só dá uma amostra por segundo. A versão melhor: cada nó numera os pacotes que envia a cada vizinho, e o vizinho conta os números que faltam. Mede só o sentido em que o vídeo vai e usa todos os pacotes, incluindo o vídeo.
  - *O que muda:* o transporte passa a ter um contador de pacotes por vizinho, em vez de um por nó. O formato do cabeçalho não muda.
  - *Custo:* médio.

- [ ] **Ir ao servidor mais perto (parte do 7).** Se o mesmo vídeo estiver nos dois servidores, o cliente pede-o ao que tiver o custo mais baixo.
  - *Depende de:* o professor dizer se os dois servidores podem ter vídeos iguais.
  - *Custo:* baixo.

---

## 4. Proposta de tirar

- **Troca de caminho sem cortes (3).** A ideia era pedir o vídeo ao pai novo antes de largar o antigo, e deitar fora os frames repetidos durante a troca. O corte que evita é o tempo de o pedido chegar ao pai novo: no CORE são milissegundos, no máximo um ou dois frames. E obrigava a tratar frames repetidos em todos os nós. Não compensa.

- **Partir os frames em pedaços (15).** Um frame tem em média 8535 bytes e num pacote cabem cerca de 1446, por isso a rede parte cada frame em 6 fragmentos, em média. Se um se perde, perde-se o frame. A ideia era sermos nós a partir os frames. **Mas isso não resolve o problema:** perder um pedaço nosso estraga o frame na mesma. Só ajudava se também pedíssemos de novo o pedaço perdido, que é outro extra, e dos caros. Fica como o código do professor faz, e o relatório explica o limite: 1% de pacotes perdidos são cerca de 6% dos frames, e 5% são cerca de 27% (estimativa a partir do tamanho dos frames). Os testes com perda fazem-se com 1 a 5%.

- **Buffer no player (16).** Guardar os frames cerca de 200 ms antes de os mostrar, para os pôr por ordem. O ganho só aparecia com muita variação do atraso, e custa 200 ms de atraso sempre. O player já deita fora os frames que chegam atrasados. Para o vídeo de teste não compensa.

- **Esconder frames perdidos (17).** Quando falta um frame, mostrar o anterior. **O player já faz isto:** a imagem só muda quando chega um frame novo.

- **Troca de servidor (8).** Se um servidor cai, quem estava a ver um vídeo passava para a cópia do outro servidor. Obrigava a mudar a forma como um vídeo é identificado: hoje é o par (servidor, número do vídeo), que vai no cabeçalho de cada pacote, e teria de passar a ser o nome. E depende de o professor deixar os dois servidores terem vídeos iguais.

---

## O que mudou em relação à lista de 3/11

1. **As rotas passam a ter renovação.** A lista antiga propunha rotas ativas até alguém as desligar. Vimos depois que o transporte não garante a ordem das mensagens, e isso pode deixar uma rota desligada para sempre.
2. **Partir os frames sai.** A lista antiga dava a entender que resolvia a perda de frames. Não resolve.
3. **Detetar a falha pela falta de vídeo sobe**, de "pode entrar depois" para logo a seguir ao básico.
4. **O pedido de socorro desce** para o fim, à espera do professor. E "estar em risco" passa a ser "ter só um vizinho vivo".
5. **Saem cinco extras** (secção 4) e entram dois novos: a renovação do pedido e a medição da perda pelos números em falta.
6. **A "opção A" do OSPF desaparece como item.** Era só outro nome para os extras dos anúncios (anunciar de novo, expirar, sem confirmação e limite de saltos), que já estão na lista.

---

## Glossário

| Termo | O que quer dizer |
|---|---|
| **Overlay** | A rede "virtual" que os nossos programas formam por cima da rede real. |
| **Nó** | Um dos nossos programas a correr numa máquina. Servidores e clientes também são nós. |
| **Vizinho** | Um nó com quem outro nó fala diretamente no overlay. |
| **Bootstrapper** | Programa que só recebe os nós novos e lhes diz o seu número e os vizinhos. |
| **Anúncio** | Mensagem que cada servidor espalha pela rede a dizer que existe e que vídeos tem. |
| **Ronda** | O número de um anúncio. Todos os anúncios que nascem do mesmo envio do servidor têm a mesma ronda. |
| **Custo** | Número que resume a qualidade de um caminho. Quanto mais baixo, melhor. |
| **Pai / avô** | O pai é o vizinho de quem um nó recebe o vídeo. O avô é o pai do pai. |
| **Reserva** | Um vizinho que pode substituir o pai se este cair. |
| **Ciclo** | Nós a mandarem o vídeo uns para os outros em círculo, sem chegar a ninguém. |
| **Margem** | Diferença mínima para valer a pena trocar de pai. |
| **JOIN / LEAVE** | Pedido para começar a receber um vídeo / aviso de que já não se quer. |
| **WITHDRAW** | Aviso de "perdi a rota para este servidor". |
| **PING** | Mensagem de teste que segue o caminho do vídeo e regista por onde passou. |
| **Sinal de vida** | Mensagem pequena só para dizer "estou vivo". Também serve para medir a ligação. |
| **Tempo de ida e volta** | Tempo desde que se envia um pacote até chegar a confirmação dele. |
| **Variação do atraso** | Quanto o atraso muda de pacote para pacote. |
| **Perda** | Percentagem de pacotes que não chegam. |
| **Salto** | Cada passagem de um nó para o seguinte no overlay. |
| **Frame** | Uma imagem do vídeo. O vídeo de teste tem 20 por segundo. |
| **Fragmento** | Pedaço de um pacote que a rede partiu por ser grande demais. |
| **Multicast** | Envio de um para um grupo de interessados, com os nós intermédios a replicar. |
| **OSPF** | O protocolo com que os routers do CORE escolhem os caminhos na rede real. |
| **Babel / EIGRP** | Dois protocolos de encaminhamento usados em redes reais. É deles que vem a regra que evita ciclos na reparação. |
| **Wireshark** | Programa que mostra os pacotes que passam numa rede. |
| **CORE** | O emulador de redes onde corremos os testes. |
