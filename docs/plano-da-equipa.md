# TP2 ESR — Plano da equipa

Atualizado a 14/11/2026 · Grupo: George, Inês, Pedro

Este documento diz o que o trabalho pede, o que já temos, o que falta e por que ordem o vamos fazer. Lê-se sozinho: tudo o que é preciso para o perceber está aqui. Substitui o plano de 26/10 e a lista de tarefas de 3/11. Em 14/11 foi atualizado com o que entrou no GitHub entre 10 e 11/11.

Há mais dois documentos ao lado deste, cada um também completo por si:

- **Desenho das rotas e do vídeo**: como os nós escolhem caminhos, pedem vídeos e reagem a falhas.
- **Extras**: o que podemos fazer além do que o guião pede, e o que vale a pena.

Tudo o que aparece como "proposta" ou "recomendação" ainda não foi decidido pelo grupo. O que aparece como "decidido" foi.

---

## 1. O que o guião pede

**O objetivo.** Entregar vídeo em tempo real de um servidor a vários clientes, sem o servidor mandar uma cópia a cada um. Para isso, um conjunto de programas nossos (os **nós**) formam uma rede própria por cima da rede normal (o **overlay**) e passam o vídeo uns aos outros. Onde dois clientes partilham parte do caminho, essa parte leva uma só cópia.

**Requisitos mínimos do guião, e como estamos em cada um:**

| O guião pede | Estado |
|---|---|
| Um nó é um programa que se arranca à mão numa máquina | Feito: `python3 -m ott <ip-do-bootstrapper>` |
| Os nós podem entrar e sair a qualquer momento | Feito: aviso de saída, deteção pelo silêncio e nova tentativa de ligação depois de uma falha da rede |
| Cada nó mantém ligações com os seus vizinhos | Feito |
| Ao arrancar, o nó conhece pelo menos um nó que o ajuda a saber os vizinhos | Feito: o bootstrapper |
| Cada nó tem uma tabela de rotas por vídeo (de onde vem, custo, para onde vai, se está ativa) | Em parte: há a tabela (pai, destinos, ativa); falta o custo e a escolha do pai |
| A topologia tem caminhos alternativos e vários clientes ao mesmo tempo | Feito: cenário 3 com 13 nós, 2 servidores e 2 vizinhos por cliente |
| As rotas são atualizadas quando entram e saem nós e quando a rede muda | Por fazer |
| Os nós reencaminham o vídeo segundo a tabela | Feito, mas só funciona quando alguém diz a cada nó quem é o pai (ver secção 4) |
| Pelo menos 2 servidores, cada um com pelo menos 2 vídeos diferentes | Em parte: há 2 vídeos e o nó aceita vários; falta testar com 2 servidores |
| As rotas são escolhidas com medidas da rede | Por fazer (o tempo de ida e volta já é medido) |

**Etapas sugeridas pelo guião** (não têm de ser feitas por ordem):

- **Etapa 0:** escolher a linguagem, o protocolo de transporte e preparar uma topologia de teste.
- **Etapa 1:** construir o overlay. Cada nó fica a conhecer os seus vizinhos e mantém as ligações vivas.
- **Etapa 2:** construir as rotas. Cada nó fica a saber por que vizinho chega cada vídeo.
- **Etapa 3:** testar os caminhos com uma mensagem de teste que segue as rotas e regista por onde passou.
- **Etapa 4:** passar o vídeo.

**Cenários de teste do guião:**

- **Cenário 1:** 4 nós (um servidor, um nó intermédio, dois clientes).
- **Cenário 2:** 6 nós (um servidor, três nós intermédios, dois clientes).
- **Cenário 3:** overlay complexo, a partir da figura 1 do guião.

**Avaliação:**

| Item | Peso | Onde estamos |
|---|---|---|
| Topologia do overlay | 10% | Feito |
| Gestão dos fluxos de vídeo | 25% | Metade: pedidos, desistências e reencaminhamento feitos; falta a escolha de caminhos |
| Protocolo de controlo | 15% | Transporte, vizinhos e pedidos feitos; faltam os anúncios |
| Protocolo de streaming | 10% | O vídeo já passa de nó em nó; falta passar com rotas automáticas |
| Solução final integrada | 30% | Por começar. Inclui a defesa e o relatório |
| Avaliações intermédias | 5% + 5% | — |

Em cada item contam três coisas: a ideia (especificação), a qualidade do código, e a qualidade dos testes e do nosso espírito crítico sobre a solução.

**Entrega:**

- Relatório em formato de artigo, **no máximo 12 páginas** (formato recomendado: LNCS, o modelo de artigos da Springer). Estrutura: introdução, arquitetura, especificação dos protocolos (formato das mensagens e interações), implementação, testes e resultados, conclusões.
- Código num zip.
- Nomes: `ESR-TP2-PLx-Gy-Rel.pdf` e `ESR-TP2-PLx-Gy-Codigo.zip` (x é o turno, y o número do grupo).
- Prazo: finais de dezembro. **Falta confirmar o dia exato.**

---

## 2. O que o professor nos disse

**Sobre o trabalho:**

- O **bootstrapper** (o programa que recebe os nós novos) só pode inicializar a rede: dar a cada nó o seu número e a lista de vizinhos. Não pode fazer mais nada depois disso, e muito menos ajudar a escolher caminhos.
- **Um nó só conhece os seus vizinhos.** Não pode conhecer a rede inteira nem a árvore por onde o vídeo passa. Decide os caminhos só com o que os vizinhos lhe dizem.
- Podemos inspirar-nos no QUIC (o protocolo do HTTP/3), mas **não usar o QUIC a sério**: fazemos um protocolo nosso com as ideias dele que nos servem.
- **Sinal de vida de 0,5 em 0,5 s é "um pouco paranoico".** Quer um mecanismo mais adaptativo, que siga algo que exista na realidade. No relatório, devemos dizer o que seguimos, em vez de justificar os valores com testes nossos.
- **Arranque:** todos os nós correm o mesmo código. Um nó que manda cerca de 3 mensagens de apresentação (HELLO) sem resposta dá o vizinho como em baixo e para de tentar. Quando esse vizinho arrancar, é ele que toma a iniciativa.

**Sobre os relatórios da cadeira:**

- Não quer o relato dos passos nem resultados colados sem explicação. Quer que digamos o que cada teste mostra e para que serve.
- **Cada número citado no texto tem de aparecer num print.** Um número sem print é como se não existisse.
- Cada print leva uma caixa à volta dos valores de que o texto fala.
- Texto simples e curto, só com os valores mais importantes.

---

## 3. O que já está decidido

| Assunto | O que decidimos | Porquê |
|---|---|---|
| Linguagem | Python 3, só com a biblioteca standard no nó | Há código base de streaming em Python (o do professor), escreve-se depressa e corre direto no CORE |
| Transporte | UDP com um protocolo nosso inspirado no QUIC | Num vídeo em tempo real não se pode esperar por pacotes perdidos, como o TCP faz. O professor sugeriu este caminho, e o protocolo de controlo é avaliado |
| Um só socket por nó | Tudo passa pela porta UDP 5000: controlo e vídeo, para todos os vizinhos | Mais simples, e é como o QUIC faz |
| Formato das mensagens | Cabeçalho binário fixo de 14 bytes; corpo das mensagens de controlo em JSON; vídeo em bytes crus | O cabeçalho fica pequeno e bem definido, o controlo é fácil de ler nos testes, e o vídeo não aumenta de tamanho |
| Fiabilidade | Só as mensagens de controlo são confirmadas e reenviadas. O vídeo não | Um frame atrasado já não serve; uma mensagem de controlo perdida estraga as rotas |
| Vizinhos vivos | Quem não dá sinal durante um certo tempo é dado como em baixo. Perder confirmações não chega para isso | Nos testes, perder confirmações deu um falso alarme com o vizinho vivo |
| Bootstrapper | Programa à parte, que só dá a cada nó o seu número e os vizinhos, lidos de um ficheiro JSON | Restrição do professor. Se cair depois do arranque, o vídeo continua |
| Identidade dos nós | Número curto dado pelo bootstrapper (ex: 3) | Lê-se bem nos logs e não fica preso ao endereço IP |
| Concorrência | O nó usa `asyncio` (uma só linha de execução, com temporizadores). O player corre num processo à parte | Sem duas coisas a mexer nas tabelas ao mesmo tempo. A janela do player bloquearia o nó |
| Topologias | Cenários 1 e 2 em redes pequenas; cenário 3 numa rede grande feita a partir da figura 1 | Começar pequeno para depurar. Na rede grande, os nós ficam só em PCs, há um 2.º servidor e cada cliente tem 2 vizinhos |
| Como nascem as rotas | Os servidores anunciam-se periodicamente a toda a rede, e os clientes pedem o vídeo pelo caminho inverso | É a primeira estratégia do guião. Falta fechar os detalhes (ver secção 7) |
| Medidas para escolher caminhos | Tempo de ida e volta, número de saltos, perda e variação do atraso | São as mais comuns. Falta o professor aceitar |
| Extra principal | Regeneração de rotas: quando um caminho falha ou piora, a rede encontra outro sozinha | Escolhido cedo para não termos de refazer código |

---

## 4. Onde estamos (14/11)

No GitHub, a última alteração é de 11/11. Os 28 testes automáticos passam.

**Feito:**

- **Transporte** (`ott/transporte.py`, `ott/protocolo.py`): envio e receção por UDP, confirmações e reenvios, sinal de vida, deteção de vizinho em baixo, medição do tempo de ida e volta. Testado no CORE no cenário 1, com perda e com um nó a cair e a voltar.
- **Bootstrapper** (`bootstrapper/`): lê o JSON do cenário e responde a cada nó com o seu número e os vizinhos.
- **Vizinhos** (`ott/__main__.py`): registo, apresentação aos vizinhos, aviso de saída (CLOSE, enviado também com `Ctrl+C`), novas tentativas de ligação aos 5, 10, 20, 40 e 60 s depois de um vizinho cair, e limpeza do estado de um vizinho que reiniciou.
- **Fluxos** (`ott/__main__.py`): tabela por vídeo (pai, destinos, ativo ou não), pedido e desistência (JOIN e LEAVE) com renovação de 2 em 2 s e expiração aos 6 s, vídeo e PING só aceites do pai, PING com o caminho e a hora de cada salto.
- **Vídeo nas pontas** (`ott/video.py`): o nó recebe os frames do programa de vídeo na própria máquina e entrega-os ao player. O vídeo recomeça no fim, com o número do frame sempre a subir. Há dois vídeos de teste em `videos/`.
- **Cenários 1, 2 e 3**: topologias do CORE e ficheiros JSON. O cenário 3 (`topologias/underlay.xml`, `configs/cenario3.json`) tem 13 nós, todos em PCs, 2 servidores e 2 vizinhos por cliente.
- **Documentos no repositório**: formato do protocolo, tabela de endereços, guia de testes no CORE, tamanho dos frames, e os requisitos do player (Tkinter e Pillow) no README.

**O que mudou em relação ao desenho proposto.** A Inês fez a parte dos fluxos de uma forma um pouco diferente:

- A escolha do pai fica fora da tabela de fluxos: há uma função, `definir_pai(fluxo, pai)`, que os anúncios vão chamar. O pai é guardado por vídeo, e não por servidor como no desenho. É compatível: quando o pai para um servidor mudar, chama-se a função para cada vídeo desse servidor.
- O pacote de vídeo continua sem a hora de envio. Fica para quando fizermos as medições no cliente.
- O caminho manual do vídeo (`--enviar-para` no nó do servidor) deixou de funcionar, porque um nó só aceita vídeo do pai e nada na linha de comando define o pai. **Neste momento não há forma de ver o vídeo pelo overlay no CORE.** Só o teste automático o faz, chamando `definir_pai` no código.

**Falta:**

- **Anúncios e escolha do pai** (George). É o que está a travar tudo: sem isto, o vídeo não passa no CORE.
- As medidas do custo, o aviso de perda de rota (WITHDRAW) e a reparação.
- Tirar do transporte a função `keepalive()` que envia o sinal de vida como mensagem fiável.
- Teste de integração no cenário 2 e prints.
- Testes com 2 servidores, testes finais e relatório.

**O ponto fraco do plano até aqui.** A maior parte da nota (fluxos 25%, streaming 10%, solução integrada 30%) depende de o vídeo passar com rotas automáticas, e isso ainda não acontece. E o relatório só tem 12 páginas: não cabem lá vinte extras bem explicados e testados.

---

## 5. Ordem de trabalho (proposta)

**Princípio: primeiro o vídeo de ponta a ponta, depois a robustez, depois os extras.** Cada fase só começa quando a anterior está a funcionar no CORE. Assim, se o tempo acabar, fica de fora um extra e não o básico.

As datas são indicativas e têm de ser ajustadas quando soubermos o dia da entrega.

| Semana | Objetivo | Pronto quando |
|---|---|---|
| **10 a 16/11** | Fechar a Etapa 1. Decidir em reunião o desenho das rotas. Começar os anúncios, a tabela e a ligação do vídeo nas pontas | No cenário 2, os 6 nós formam o overlay sozinhos, e um nó que sai e volta é readmitido. As decisões da secção 7 estão tomadas |
| **17 a 23/11** | **Vídeo de ponta a ponta no cenário 2**, na versão mais simples: anúncios com custo, pedido do cliente, reencaminhamento e mensagem de teste dos caminhos | Um cliente vê o vídeo. Com dois clientes, a ligação comum leva uma só cópia. A mensagem de teste mostra o caminho |
| **24 a 30/11** | Robustez: custo com as quatro medidas, troca de caminho com margem, rotas que expiram, aviso de perda de rota e reparação. Dois servidores com dois vídeos cada. Topologia grande pronta | No cenário 2, cortar ou piorar a ligação entre dois nós intermédios faz o vídeo passar por outro caminho. Os quatro vídeos funcionam ao mesmo tempo |
| **1 a 7/12** | Cenário 3. Deteção rápida de falhas pelo vídeo. Medições no cliente. Comparação com a solução sem overlay | Os testes da secção 8 correm todos no cenário 3 |
| **8 a 14/12** | Testes finais com prints. Extras que sobrarem. Primeiro rascunho do relatório | Há um print para cada número que vai para o relatório |
| **15/12 até à entrega** | Relatório, revisão, zip do código e preparação da defesa | — |

---

## 6. Tarefas de cada um (atualizado a 14/11)

Cada pessoa trabalha num ramo seu do git (ex: `george/rotas`) e junta-o à `main` quando estiver testado. Antes de continuar a trabalhar, junta a `main` ao seu ramo, para os conflitos não se acumularem. **Entre 10 e 11/11 os 8 commits entraram todos direto na `main`.**

### George: transporte e escolha de caminhos

- [ ] **Juntar o estado dos fluxos num só sítio** (passou da Inês para o George, a 14/11, porque os anúncios mexem nesse código). Hoje o pai de cada vídeo está guardado em dois sítios (`tabela_fluxos` e `pais_video`) e os destinos também (`tabela_fluxos` e `rotas_video`), e o código tem de os manter iguais à mão. Fica só a `tabela_fluxos`.
- [ ] **Anúncios, versão mínima** (primeiro, porque trava o resto): o servidor anuncia-se de 2 em 2 s; cada nó guarda o último anúncio de cada vizinho, escolhe o pai pelo custo mais baixo, chama `definir_pai` para cada vídeo desse servidor e passa o anúncio aos outros vizinhos. Custo, para começar: o tempo de ida e volta que o transporte já mede, mais 1 por salto. Mais a opção `--ver` do cliente passar a pedir o vídeo assim que houver pai.
- [ ] Reservar no `ott/protocolo.py` os códigos que faltam: ANNOUNCE (0x10) e WITHDRAW (0x13). O JOIN, o LEAVE e o PING já lá estão.
- [ ] Tirar a função `keepalive()` fiável do `ott/transporte.py`.
- [ ] Tirar a opção `--enviar-para` e o código ligado a ela, que já não serve: o vizinho deita fora o vídeo, porque não vem do pai.
- [ ] O transporte passa a dar três medidas por vizinho: tempo de ida e volta mínimo, variação do atraso e perda. Para isso, o sinal de vida passa a ser enviado a intervalos certos, mesmo quando há vídeo a passar.
- [ ] Depois de o vídeo funcionar: rotas que expiram, aviso de perda de rota, troca com margem e reparação.

### Inês: vizinhos e fluxos

- [x] Aviso de saída (CLOSE).
- [x] Novas tentativas de ligação a vizinhos em baixo (5, 10, 20, 40 e 60 s, depois de minuto a minuto).
- [x] Reinício rápido: um HELLO novo de um vizinho já confirmado limpa o estado dele.
- [x] Tabela de fluxos, JOIN e LEAVE com renovação, vídeo e PING só aceites do pai.
- [ ] Depois de um CLOSE, não voltar a tentar ligar ao vizinho que saiu. Hoje o nó continua a mandar-lhe HELLO de minuto a minuto, e quando o vizinho voltar é ele que se apresenta.
- [ ] Teste de integração no cenário 2, com prints (com o George, quando houver anúncios).

### Pedro: rede, vídeo e testes

- [x] Topologia grande (`topologias/underlay.xml`), JSON do cenário 3 e tabela de endereços.
- [x] Ligar o vídeo nas pontas, vídeo em ciclo e um 2.º vídeo.
- [ ] Confirmar no CORE os dois pontos que deixou em aberto na tabela de endereços (a ligação R5–R7 e o endereço do S1).
- [ ] O número do frame do vídeo só tem 16 bits: ao fim de 65 535 frames (cerca de 55 minutos a 20 por segundo) o programa de vídeo deixa de enviar. Não afeta testes curtos, mas convém dar a volta ao número (e o player aceitar a volta) antes da defesa.
- [ ] Opção `--servidor` repetida sem porta (`--servidor --servidor`) dá as duas vezes a porta 24000 e o nó não arranca. Ou se obriga a dar a porta a partir do 2.º vídeo, ou se soma 1 a cada um.
- [ ] Depois: medições no cliente (com a hora de envio no pacote de vídeo) e comparação com a solução sem overlay.

**Atenção:** o player precisa do Tkinter e do Pillow, que não vêm com o Python. Os comandos para os instalar estão no README.

---

## 7. Decisões a tomar na próxima reunião

São as que bloqueiam código. Cada uma tem as opções e a recomendação. A 7.2, a 7.3 e a 7.4 tocam no trabalho do George e têm de ficar fechadas antes das medidas do custo; a versão mínima dos anúncios pode começar já.

### 7.1 O pedido de um vídeo fica "para sempre" ou tem de ser renovado?

**Já resolvido na prática:** a Inês implementou a opção B (com renovação). Fica aqui o porquê, para o relatório.

Quando um nó pede um vídeo ao pai, o pai passa a enviar-lho.

- **Opção A, estado fixo:** o pai envia até receber uma desistência, ou até o nó ser dado como em baixo. Era a proposta de 3/11.
- **Opção B, com renovação:** além disso, o nó repete o pedido de 2 em 2 s. Se o pai ficar 6 s sem renovação, deixa de enviar.

**Recomendação: B.** A opção A tem dois problemas que só vimos depois:

1. O transporte garante que as mensagens chegam, mas não garante a ordem. Num dos nossos testes, a mensagem 2 chegou antes da 1. Se um cliente desistir e voltar a pedir logo a seguir, a desistência reenviada pode chegar depois do pedido novo, e o pai deixa de enviar para sempre.
2. Se um nó reiniciar em menos de 3,5 s, o pai não nota e continua a mandar-lhe vídeo que ele já não quer.

Com a renovação, qualquer erro destes corrige-se sozinho em poucos segundos. É como o encaminhamento de multicast nos routers a sério (o protocolo PIM) funciona.

### 7.2 Como juntar as quatro medidas num custo só?

- **Opção A, soma com pesos:** custo de uma ligação = tempo de ida e volta + variação do atraso + um valor por cada 1% de perda + um valor por salto.
- **Opção B, ordem de prioridade:** primeiro excluir ligações com perda acima de um limite, depois escolher pelo tempo, e os saltos só para desempatar.

**Recomendação: A.** A regra que evita ciclos na reparação precisa de um custo único que aumenta a cada salto. E um limite de perda cria um degrau: uma ligação com a perda a oscilar à volta do limite faria a rota saltar a toda a hora. Valores iniciais propostos: 20 ms por cada 1% de perda e 1 ms por salto, a afinar nos testes.

### 7.3 Como medir a perda?

- **Opção A, pelos sinais de vida:** contar quantos dos últimos 20 sinais de vida ficaram sem confirmação. Simples, mas mede a ida e a volta juntas e só dá uma amostra por segundo.
- **Opção B, pelos buracos na numeração:** cada nó numera os pacotes que envia a cada vizinho, e o vizinho conta os números que faltam. Mede só o sentido em que o vídeo vai e usa todos os pacotes, mas obriga a mexer mais no transporte (hoje há um contador por nó, não por vizinho).

**Recomendação: começar pela A**, escondida atrás de uma função do transporte (`perda(vizinho)`). Se sobrar tempo, troca-se pela B sem mexer no resto.

### 7.4 De quanto em quanto tempo um nó dá sinal de vida?

Hoje: um sinal ao fim de 0,5 s sem enviar nada, e 3,5 s de silêncio dão o vizinho como em baixo. O professor achou exagerado.

**Recomendação:**

1. Cada nó diz, na mensagem de apresentação (HELLO), de quanto em quanto tempo vai dar sinal: 1 s por omissão. O vizinho dá-o como em baixo ao fim de 3 intervalos sem ouvir nada. Os valores passam a ser combinados entre cada par de vizinhos, em vez de estarem fixos no código. É o modelo do BFD, o protocolo que os routers usam para detetar falhas depressa.
2. O limite nunca é menor do que 3 vezes o tempo de reenvio medido para esse vizinho. É a regra do QUIC, e faz o limite esticar sozinho numa ligação lenta.
3. Quando há vídeo a passar, a falta de vídeo é o sinal mais rápido. Chegam 20 frames por segundo, por isso 300 ms sem nenhum (6 frames seguidos) já mostram que algo falhou, sem esperar pelos 3 s.

**Porque o 0,5 s deixa de ser preciso:** foi escolhido para não haver falsos alarmes com 20 a 30% de perda. Mas com 20% de perda estimamos que se perdem cerca de 74% dos frames, por isso o vídeo já não se vê. Não faz sentido afinar o sistema para essa situação. Com 1 a 5% de perda, 3 intervalos de 1 s chegam bem.

Isto obriga a duas mudanças no código: o HELLO passa a ter corpo (hoje o nó rejeita um HELLO que não venha vazio) e o transporte passa a ter um limite por vizinho.

### 7.5 Partir os frames em pedaços?

Um frame tem em média 8535 bytes e num pacote só cabem cerca de 1446. Hoje cada frame vai num só pacote UDP, que a rede parte em 6 fragmentos, em média. Se um fragmento se perde, perde-se o frame.

A ideia de sermos nós a partir os frames **não resolve isto**: perder um pedaço estraga o frame na mesma. Só ajudaria se também pedíssemos de novo o pedaço perdido, que é mais um extra.

**Recomendação: não partir.** Fica como o código do professor faz, e o relatório explica o limite. Os testes com perda fazem-se com 1 a 5%, não com 20%. É o que o código faz hoje.

### 7.6 Que extras entram?

A decisão é do grupo. A recomendação, em resumo:

- **Entram com o básico**, porque fazem parte do desenho das rotas: trocar logo para um vizinho de reserva quando o pai cai; avisar quem recebe de nós quando ficamos sem rota; mudar de caminho quando o atual piora, com uma margem; rotas que expiram se o servidor se calar; anúncios sem confirmação; limite de saltos; a lista de vídeos nos anúncios; medições no cliente; a hora de passagem em cada nó na mensagem de teste; e a comparação com a solução sem overlay.
- **Logo a seguir:** detetar a falha pela falta de vídeo (300 ms em vez de 3 s).
- **Só se sobrar tempo:** o pedido de socorro com o "avô" (e só se o professor aceitar), os logs com desenho da árvore, o leitor do nosso protocolo para o Wireshark, a comparação com o encaminhamento da rede real, e medir a perda pelos buracos na numeração.
- **Proposta de tirar:** a troca de caminho sem cortes, partir os frames, o buffer no player, esconder frames perdidos (o player já o faz) e a troca de servidor.

Cada um destes está explicado por inteiro no documento dos extras.

---

## 8. Testes que o relatório precisa

Em cada item da avaliação conta a qualidade dos testes e do nosso espírito crítico. Estes são os testes mínimos; cada um dá um ou dois prints.

| Teste | O que mostra |
|---|---|
| Os 6 nós do cenário 2 arrancam e formam o overlay | A construção do overlay funciona só com o endereço do bootstrapper |
| Um cliente pede um vídeo e a mensagem de teste mostra o caminho | As rotas são as esperadas e o vídeo chega |
| Dois clientes veem o mesmo vídeo; mede-se o tráfego à saída do servidor com e sem overlay | O overlay envia uma só cópia na parte comum. É o objetivo do trabalho |
| O último cliente desiste e o tráfego para | As rotas ficam inativas quando ninguém vê |
| No cenário 2, corta-se a ligação entre dois nós intermédios com um cliente a ver (perda de 100% só entre esses dois, com o `tc`) | O vídeo volta por outro caminho, e em quanto tempo |
| No cenário 3, para-se um nó intermédio com um cliente a ver | O mesmo, quando é um nó inteiro que cai. No cenário 2 não dá para testar: cada cliente só tem um vizinho, e parar esse vizinho deixa-o isolado |
| Piora-se uma ligação (atraso ou 1 a 5% de perda) | A rota muda para um caminho melhor, e não fica a saltar |
| Dois servidores com dois vídeos cada, ao mesmo tempo | O requisito dos vários fluxos |
| Um nó entra com o vídeo já a passar | As rotas atualizam-se com a entrada de nós |

---

## 9. Dúvidas para o professor

1. **Um servidor ou dois?** O texto do guião diz "vamos considerar que só há um servidor de streaming" e "como só há um fluxo, apenas uma rota". A lista de requisitos pede "pelo menos, dois servidores, cada um a disponibilizar, no mínimo, dois fluxos de vídeo distintos". Estamos a seguir a lista de requisitos. Está certo?
2. **Os dois servidores podem ter vídeos iguais?** Se sim, o cliente pode ir buscar o vídeo ao servidor mais perto. Se não, cada vídeo só existe num servidor.
3. **Aceita as medidas que escolhemos** (tempo de ida e volta, saltos, perda e variação do atraso), todas medidas pelo nosso próprio protocolo?
4. **Aceita a exceção do "avô"?** Num dos extras, um nó que está em risco de ficar isolado fica a saber quem é o vizinho do seu vizinho, para se ligar a ele se perder todas as ligações. É a única vez que um nó conhece alguém além dos vizinhos.
5. **Aceita os extras** que propomos?
6. **Datas:** o dia exato da entrega e o que se espera em cada avaliação intermédia.

---

## 10. Regras de trabalho

- **Git:** um ramo por pessoa (ex: `ines/fluxos`), juntado à `main` quando estiver testado. Antes de continuar a trabalhar, juntar a `main` ao ramo.
- **Não vão para o git:** capturas de rede e logs. Os dois vídeos de teste estão em `videos/` (decidido pelo Pedro a 10/11, para os nós do CORE os terem à mão); outros vídeos não entram.
- **Prints:** vão para `tp2/relatorio/evidencias/`, com nomes do tipo `E2_03_<o-que-mostra>.png`, e cada um leva caixa nos valores que o texto usa.
- **Perda nos testes:** aplica-se com o comando `tc` nas duas pontas da ligação, porque no nosso caso o diálogo do CORE não a aplicou. Os comandos estão em `docs/testes.md`, no repositório.
- **Parar um nó:** `Ctrl+C` no terminal dele. Nunca `pkill` nem `killall`, que no CORE matam os programas de todos os nós.
- **Termos técnicos:** quem usar um termo novo num documento explica-o no próprio documento.

---

## Glossário

| Termo | O que quer dizer |
|---|---|
| **Overlay** | A rede "virtual" que os nossos programas formam por cima da rede real. Dois vizinhos no overlay não precisam de estar ligados por um cabo. |
| **Nó** | Um dos nossos programas a correr numa máquina. Servidores e clientes também são nós. |
| **Vizinho** | Um nó com quem outro nó fala diretamente no overlay. |
| **Bootstrapper** | Programa que só recebe os nós novos e lhes diz o seu número e os vizinhos. |
| **Fluxo** | Um vídeo de um servidor. É identificado pelo par (servidor, número do vídeo). |
| **Rota** | Para um nó, a informação de por que vizinho chega um vídeo e para que vizinhos o deve enviar. |
| **Anúncio** | Mensagem que cada servidor espalha pela rede a dizer que existe e que vídeos tem. |
| **Pai** | O vizinho de quem um nó recebe os vídeos de um servidor. |
| **JOIN / LEAVE** | Pedido para começar a receber um vídeo / aviso de que já não se quer. |
| **HELLO** | Mensagem de apresentação entre vizinhos, quando um deles arranca. |
| **CLOSE** | Aviso de saída de um nó. |
| **Sinal de vida (keepalive)** | Mensagem pequena só para dizer "estou vivo". |
| **Tempo de ida e volta (RTT)** | Tempo desde que se envia um pacote até chegar a confirmação dele. |
| **Variação do atraso (jitter)** | Quanto o atraso muda de pacote para pacote. |
| **Perda** | Percentagem de pacotes que não chegam. |
| **Salto** | Cada passagem de um nó para o seguinte no overlay. |
| **Frame** | Uma imagem do vídeo. O vídeo de teste tem 20 por segundo. |
| **Fragmento** | Pedaço de um pacote que a rede partiu por ser grande demais. |
| **UDP / TCP** | Duas formas de enviar dados. UDP: pacotes soltos, sem garantias, rápido. TCP: tudo chega e por ordem, mas com esperas. |
| **QUIC** | Protocolo moderno (o do HTTP/3) que dá garantias por cima de UDP. Foi a inspiração do nosso. |
| **BFD** | Protocolo usado nos routers para detetar depressa que um vizinho deixou de responder. |
| **PIM** | Protocolo com que os routers encaminham multicast (um envio para um grupo de interessados). |
| **CORE** | O emulador de redes onde corremos os testes. |
| **tc** | Comando do Linux que simula perda ou atraso numa ligação. |
| **Ramo / merge** | No git, um ramo é uma linha de trabalho separada. O merge junta esse trabalho ao código principal (a `main`). |
| **LNCS** | Modelo de artigo da editora Springer, recomendado para o relatório. |
