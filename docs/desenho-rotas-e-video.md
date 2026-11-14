# TP2 ESR — Desenho das rotas e do vídeo (proposta)

Atualizado a 14/11/2026 · Grupo: George, Inês, Pedro

Este documento explica como o vídeo vai de um servidor até aos clientes: como cada nó descobre o melhor caminho, como um cliente pede um vídeo, como o vídeo é reencaminhado e o que acontece quando algo falha. Cobre as Etapas 2, 3 e 4 do guião. Lê-se sozinho.

**Estado:** é uma proposta para o grupo discutir. Já estão decididas em grupo três coisas: os servidores anunciam-se e os clientes pedem o vídeo pelo caminho inverso; as medidas usadas são o tempo de ida e volta, os saltos, a perda e a variação do atraso; e queremos que a rede recupere sozinha de falhas. Tudo o resto (formatos, tempos, regras) é proposta. As escolhas em aberto estão juntas na secção 13.

**O que já está no código (14/11):** os pedidos e desistências com renovação (secção 8), o reencaminhamento do vídeo só a partir do pai (secção 10) e o PING (secção 11). Duas diferenças em relação a este documento: o pai é guardado por vídeo e não por servidor, e escolhe-se chamando a função `definir_pai(fluxo, pai)`; e o pacote de vídeo ainda não leva a hora de envio. Faltam os anúncios, o custo, a escolha do pai e tudo o que está na secção 9.

---

## 1. A ideia em poucas linhas

1. Cada servidor diz periodicamente "estou aqui e tenho estes vídeos". Essa mensagem, o **anúncio**, passa de vizinho em vizinho por toda a rede.
2. Cada nó, antes de passar o anúncio, soma-lhe o custo da ligação por onde ele chegou. Assim, o anúncio que um nó recebe diz quanto custa chegar ao servidor por aquele vizinho.
3. Cada nó fica com o vizinho que lhe dá o caminho mais barato. Chamamos-lhe o **pai**: é dele que os vídeos desse servidor vão chegar. (No guião, este campo da tabela chama-se "Origem".)
4. Enquanto ninguém pedir um vídeo, não passa vídeo nenhum. As rotas existem, mas estão inativas.
5. Um cliente que quer ver um vídeo manda um pedido, o **JOIN**, ao seu pai. O pai manda ao pai dele, e assim sucessivamente, até ao servidor ou até um nó que já esteja a receber esse vídeo.
6. A partir daí, cada nó do caminho reencaminha o vídeo para quem lho pediu. Se dois clientes pedem o mesmo vídeo, a parte comum do caminho leva uma só cópia.
7. Se um nó cai ou uma ligação piora, os nós afetados escolhem outro pai e voltam a pedir o vídeo por aí.

**As duas regras do professor que este desenho respeita:**

- Um nó só conhece os seus vizinhos. Aqui, cada nó só sabe o que cada vizinho lhe anunciou (um custo) e a quem está a enviar. Nunca vê o caminho inteiro.
- O bootstrapper só inicializa a rede. Aqui, não entra em nada.

---

## 2. O exemplo usado neste documento

É o cenário 2 do guião: um servidor (S1), três nós intermédios (N1, N2, N3) e dois clientes (C1, C2).

```
   C2 ── N2
         │ ╲
         │  N1 ── S1
         │ ╱
   C1 ── N3
```

As ligações do overlay são: S1–N1, N1–N2, N1–N3, N2–N3, N2–C2 e N3–C1.

Nos exemplos, o custo de cada ligação é: S1–N1 = 2,0 · N1–N2 = 2,2 · N1–N3 = 2,0 · N2–N3 = 2,1 · N2–C2 = 2,0 · N3–C1 = 2,0. São valores inventados para o exemplo, parecidos com os que se medem no CORE sem atraso configurado.

---

## 3. O que o transporte já nos dá

O transporte é a parte do nó que envia e recebe pacotes. Está feito, e quem programa as rotas só precisa de saber isto:

- **Mensagem fiável:** chega ao vizinho exatamente uma vez, ou então o vizinho é dado como em baixo. **Não garante a ordem:** duas mensagens fiáveis podem chegar trocadas.
- **Mensagem não fiável:** é enviada uma vez; pode perder-se.
- **Aviso de vizinho em baixo:** quando um vizinho fica em silêncio tempo demais, o transporte avisa.
- **Tempo de ida e volta** até cada vizinho.
- **Fluxo no cabeçalho:** cada pacote tem um campo com o par (servidor, vídeo). Nas mensagens que não são de um vídeo, vai a zeros.

**O que falta acrescentar ao transporte** (tarefa do George):

- Enviar o sinal de vida a intervalos certos, mesmo com vídeo a passar. Hoje só é enviado quando a ligação está parada. Com vídeo a passar nos dois sentidos deixaria de haver medições.
- Três funções por vizinho, que o resto do código usa sem saber como são calculadas: `rtt_min(vizinho)`, `jitter(vizinho)` e `perda(vizinho)`.

---

## 4. O que cada nó guarda

**Por cada servidor de que já ouviu falar:**

- O **último anúncio de cada vizinho** sobre esse servidor: a ronda, o custo que o vizinho anunciou e quanto custaria ir por ele.
- O **pai**: o vizinho escolhido.
- O que o próprio nó **já anunciou**: a última ronda e o menor custo que anunciou nela. Serve para a regra que evita ciclos.
- A **lista de vídeos** desse servidor.

**Por cada fluxo** (um vídeo de um servidor):

- Os **destinos**: os vizinhos que pediram esse vídeo. Num cliente, o player local também conta como destino.
- O **estado**: ativo se tem pelo menos um destino, inativo se não tem.

Com dois servidores, tudo isto existe uma vez por servidor. O pai para o S1 e o pai para o S2 podem ser vizinhos diferentes.

Isto é a tabela de rotas que o guião pede. Para o N1, com os dois clientes a ver o vídeo 0 do S1, seria:

```
Fluxo: (S1, vídeo 0)   Origem: S1   Custo: 2,0   Destinos: N2, N3   Estado: ativa
```

---

## 5. Os anúncios

### O que leva um anúncio

| Campo | O que é | Exemplo |
|---|---|---|
| `srv` | O número do servidor | 1 |
| `ronda` | O número deste anúncio. O servidor aumenta-o cada vez que anuncia | 57 |
| `videos` | Os vídeos do servidor: número e nome | `{"0": "filme", "1": "desenhos"}` |
| `rtt`, `jitter`, `perda`, `saltos` | As quatro medidas somadas desde o servidor até quem envia o anúncio | 2.0, 0.1, 0.0, 1 |

O anúncio diz sempre **quanto custa a quem o envia** chegar ao servidor. Quem o recebe soma a ligação entre os dois.

### Quando se envia

- **O servidor** envia um anúncio a todos os seus vizinhos de 2 em 2 segundos, com as medidas a zero. O período leva uma pequena variação ao acaso (mais ou menos 10%), para os dois servidores não anunciarem sempre no mesmo instante.
- **Os outros nós** só anunciam quando a sua rota muda: chegou uma ronda nova do pai, o custo mudou, ou trocaram de pai. Enviam a todos os vizinhos **menos ao pai**.

A ronda começa no valor do relógio (os segundos desde 1970), e não em 1. Assim, um servidor que reinicia anuncia logo rondas mais altas do que as antigas, e os vizinhos não as deitam fora por parecerem velhas. Nos exemplos deste documento usamos números pequenos, como 57, só para se ler melhor.

Os anúncios **não são confirmados nem reenviados**. Se um se perde, o seguinte substitui-o 2 segundos depois.

### O que um nó faz quando recebe um anúncio

Chega um anúncio do vizinho V sobre o servidor S:

1. **Filtrar.** Deita-o fora se a ronda for mais antiga do que a última que já recebeu de V, ou se tiver mais de 16 saltos (uma rede de segurança contra ciclos causados por um erro nosso).
2. **Guardar.** Fica como "o último anúncio de V sobre S". Calcula quanto custaria ir por V: o custo anunciado mais o custo da ligação a V (secção 6).
3. **Escolher o pai** (secção 7).
4. **Anunciar**, se a rota mudou: envia aos outros vizinhos a ronda do pai e o seu próprio custo.

### Exemplo: a ronda 57 no cenário 2

1. O S1 envia ao N1: ronda 57, custo 0.
2. O N1 soma a ligação S1–N1 (2,0). Fica com custo 2,0 e pai S1. Envia "custo 2,0" ao N2 e ao N3.
3. O N2 soma 2,2: custo 4,2, pai N1. Envia "custo 4,2" ao N3 e ao C2.
   O N3 soma 2,0: custo 4,0, pai N1. Envia "custo 4,0" ao N2 e ao C1.
4. O N2 recebe do N3 "custo 4,0". Pelo N3 custaria 4,0 + 2,1 = 6,1, pior do que 4,2. Fica com o N1 e **guarda o N3 como alternativa**.
   O N3 recebe do N2 "custo 4,2". Pelo N2 custaria 6,3. Fica com o N1 e guarda o N2.
5. O C1 fica com pai N3 e custo 6,0. O C2 fica com pai N2 e custo 6,2.

Cada nó enviou o anúncio uma vez. Ninguém ficou a conhecer o caminho: o C1 só sabe "pelo N3 custa 6,0".

---

## 6. O custo de um caminho

### As quatro medidas

| Medida | Como se mede | Porque interessa |
|---|---|---|
| **Tempo de ida e volta** | Pelo tempo entre enviar um sinal de vida e receber a confirmação. Usa-se o **mínimo das últimas 10 medições**, e não a média | Um caminho lento atrasa o vídeo. A média é sensível a picos: num teste nosso, uma só medição má levou-a de 1 ms para 400 ms |
| **Variação do atraso** | Quanto as medições do tempo de ida e volta variam entre si. O transporte já calcula este valor | Um caminho instável faz os frames chegarem aos solavancos |
| **Perda** | A percentagem dos últimos 20 sinais de vida que ficaram sem confirmação | Um pacote perdido estraga um frame inteiro |
| **Saltos** | Cada nó soma 1 | Cada nó a mais é mais um programa a reencaminhar e mais um sítio onde pode falhar |

Cada nó mede a ligação a cada vizinho com os seus próprios sinais de vida.

### A fórmula (proposta)

```
custo de uma ligação = tempo de ida e volta mínimo (ms)
                     + variação do atraso (ms)
                     + 20 × perda (%)
                     + 1
```

O custo de um caminho é a soma dos custos das suas ligações.

- **O "+ 1" é o salto.** Entre dois caminhos com medidas quase iguais, ganha o que tem menos nós.
- **20 por cada 1% de perda** quer dizer: preferimos um caminho com mais 20 ms a um caminho que perde mais 1% dos pacotes. A razão é que a perda pesa muito no vídeo. Cada frame vai, em média, em 6 fragmentos, e basta perder um para perder o frame: 1% de pacotes perdidos são cerca de 6% dos frames, e 5% são cerca de 27% (estimativa feita a partir do tamanho dos frames do vídeo de teste).
- O anúncio leva as quatro medidas em separado, e cada nó aplica a fórmula. Se quisermos mudar os pesos, não é preciso mudar o formato das mensagens.

O custo não é o atraso real do vídeo. É só um número para comparar caminhos. Se ainda não houver medições de um vizinho (por exemplo, acabou de arrancar), usa-se 100 ms até haver.

Exemplos: uma ligação com 1,0 ms de ida e volta, 0,1 ms de variação e sem perda custa 2,1. A mesma ligação com 5% de perda custa 102,1.

---

## 7. Escolher o pai, e quando trocar

### Regra 1: ficar com o pai enquanto ele servir

Um nó sem pai fica com o vizinho que dá o custo mais baixo. Depois disso, **só troca em três casos**:

- o pai caiu (o transporte avisa, ou o pai mandou um aviso de saída);
- o pai avisou que perdeu a rota (secção 9);
- outro vizinho é **claramente melhor**.

### Regra 2: "claramente melhor" tem uma margem

O nó só troca se o custo pelo outro vizinho for mais baixo em mais de **15%** e em mais de **2 unidades** de custo. Sem margem, dois caminhos parecidos fariam o nó andar sempre a trocar, porque as medidas oscilam um pouco. E cada troca é um pequeno corte no vídeo.

A comparação só se faz entre anúncios **da mesma ronda**. Comparar o anúncio novo de um vizinho com o anúncio antigo do pai seria comparar medidas de momentos diferentes.

É esta regra que faz a rede reagir quando uma ligação piora: na ronda seguinte o custo por esse caminho sobe e, se a diferença passar a margem, o nó troca.

### Regra 3: nunca escolher alguém que recebe através de mim

Se o N3 escolhesse como pai um nó que recebe o vídeo através do próprio N3, o vídeo ficava a andar em círculo. O N3 não conhece a árvore, por isso não sabe quem recebe através dele. Mas há uma regra simples que o garante:

> Um vizinho só pode ser escolhido como pai se o seu anúncio for de uma **ronda mais nova do que a última que eu anunciei**, ou da **mesma ronda com um custo menor do que o menor custo que eu anunciei** nessa ronda.

Porquê:

- Quem recebe através de mim só conhece as rondas que eu já anunciei. Um vizinho com uma ronda mais nova soube dela por outro lado.
- Quem recebe através de mim anuncia sempre um custo **maior** do que o meu, porque soma pelo menos uma ligação. Um vizinho que anuncia menos do que eu não pode estar atrás de mim.

No exemplo: o N2 anunciou 4,2 e ouviu o N3 anunciar 4,0. Como 4,0 é menor, o N3 é uma **reserva válida** para o N2. Ao contrário, o N3 anunciou 4,0 e ouviu o N2 anunciar 4,2: o N2 não é reserva válida para o N3, porque podia estar a receber através dele.

Esta regra vem de protocolos de encaminhamento reais (o Babel e o EIGRP), onde se chama condição de viabilidade.

### Quando o pai muda

O nó anuncia o custo novo aos vizinhos. E, por cada vídeo que está a reencaminhar desse servidor, manda um JOIN ao pai novo e um LEAVE ao antigo, se este ainda estiver vivo.

---

## 8. Pedir e largar um vídeo

### As mensagens

- **JOIN:** "quero receber este vídeo". O vídeo vai identificado no campo do fluxo do cabeçalho.
- **LEAVE:** "já não quero este vídeo".

Ambas vão sempre para o **pai**. O primeiro JOIN e o LEAVE são fiáveis.

### O que um nó faz

**Recebe um JOIN do vizinho V para um vídeo:**

1. Acrescenta V aos destinos desse vídeo.
2. Se já estava a receber esse vídeo, não faz mais nada: passa a enviá-lo também a V.
3. Se não estava, manda um JOIN ao seu próprio pai. Se ainda não tiver pai para esse servidor, manda-o assim que tiver. Se o nó for o servidor desse vídeo, começa a enviar.

Um JOIN que venha do próprio pai é ignorado e fica registado no log: seria o início de um ciclo, que a regra 3 da secção 7 deve impedir.

**Recebe um LEAVE de V:**

1. Tira V dos destinos.
2. Se não ficou nenhum destino, manda um LEAVE ao pai e o vídeo fica inativo nesse nó.

**Um vizinho é dado como em baixo, ou avisa que vai sair:** é tratado como um LEAVE de todos os vídeos que ele tinha pedido.

### Exemplo

1. O C1 pede o "filme": JOIN para o N3.
2. O N3 ainda não o recebe. Põe o C1 nos destinos e manda JOIN ao N1.
3. O N1 põe o N3 nos destinos e manda JOIN ao S1.
4. O S1 põe o N1 nos destinos e começa a enviar. O vídeo desce S1 → N1 → N3 → C1.
5. O C2 pede o mesmo vídeo: JOIN para o N2, que manda JOIN ao N1.
6. O N1 **já recebe** o vídeo. Só acrescenta o N2 aos destinos. O S1 nem fica a saber.

A ligação S1–N1 leva uma só cópia para os dois clientes.

### A renovação do pedido (proposta)

Além do JOIN inicial, cada nó **repete o JOIN de 2 em 2 segundos** por cada vídeo que está a receber. Estas repetições não são fiáveis. Se um nó ficar **6 segundos** sem renovação de um destino, tira-o, como se tivesse recebido um LEAVE.

Sem isto, as rotas ficam ativas "para sempre" até alguém as desligar, e isso falha em três situações:

- **Mensagens trocadas.** O transporte não garante a ordem. Num teste nosso, a mensagem 2 chegou antes da 1. Se um cliente largar um vídeo e o pedir outra vez logo a seguir, o LEAVE reenviado pode chegar depois do JOIN novo, e o pai deixa de enviar. Com a renovação, o vídeo volta em 2 segundos.
- **Reinício rápido.** Um nó que reinicia em menos tempo do que o limite de silêncio não é dado como em baixo. O pai continua a mandar-lhe vídeo que ele já não quer. Com a renovação, isso para ao fim de 6 segundos.
- **Erros nossos.** Qualquer estado errado que fique numa tabela corrige-se sozinho.

É assim que funciona o multicast nos routers (o protocolo PIM): os pedidos são renovados periodicamente e expiram se não forem.

---

## 9. Quando algo falha

### Aviso de perda de rota (WITHDRAW)

Um nó que perde o pai e não tem reserva válida fica sem rota para esse servidor. Avisa logo todos os vizinhos com um WITHDRAW ("perdi a rota para o servidor S"). Quem recebia dele trata isso como perder o pai, e procura outro. Sem este aviso, cada nó só daria pelo problema quando os anúncios deixassem de chegar.

### Rotas que expiram

O anúncio de um vizinho só vale **6 segundos** (3 períodos). Se não chegar outro, é apagado. Cobre o caso de um servidor que morre: os anúncios param e as rotas para ele desaparecem de toda a rede.

### O que acontece em cada caso

| O que acontece | Como se nota | O que os nós fazem | Tempo sem vídeo |
|---|---|---|---|
| **Cai a ligação N1–N2**, com o C2 a ver | O N2 deixa de ouvir o N1 | O N2 tem reserva válida (o N3): troca logo e manda-lhe um JOIN. O N1 tira o N2 dos destinos | O tempo de dar pelo silêncio, cerca de 3 s |
| **Cai a ligação N1–N3**, com o C1 a ver | O N3 deixa de ouvir o N1 | O N3 não tem reserva válida. Avisa o C1 e o N2 (WITHDRAW). Na ronda seguinte chega o anúncio do N2 com uma ronda mais nova, que já pode aceitar: o N2 passa a ser o pai | Cerca de 3 s, mais até 2 s de espera pela ronda |
| **Cai o N3**, com o C1 a ver | O C1 deixa de ouvir o N3 | O C1 só tem um vizinho, por isso fica sem vídeo até o N3 voltar. No cenário 3, cada cliente tem 2 vizinhos e este caso tem solução | Até o N3 voltar |
| **Cai o servidor** | O N1 deixa de o ouvir | O WITHDRAW desce por toda a rede e as rotas para esse servidor desaparecem. Quando o servidor volta, os anúncios voltam e os clientes pedem o vídeo outra vez | Enquanto o servidor estiver em baixo |
| **Um cliente fecha o player** | Manda um LEAVE | O LEAVE sobe até ao primeiro nó que ainda tenha outros destinos | — |
| **Um cliente morre sem avisar** | Silêncio, ou falta de renovação | O pai tira-o dos destinos, como num LEAVE | — |
| **Uma ligação piora** (mais atraso ou perda) | O custo por esse caminho sobe nas rondas seguintes | Se outro caminho for melhor pela margem, o nó troca | Um corte curto na troca |
| **Entra um nó novo** | Apresenta-se aos vizinhos | Recebe os anúncios na ronda seguinte e passa a poder servir de caminho | — |

### Detetar a falha mais depressa (para depois)

Com vídeo a passar chega um frame a cada 50 ms. Um nó que fica 300 ms sem receber vídeo do pai (6 frames seguidos) pode começar logo a reparação, em vez de esperar pelos 3 segundos de silêncio. O tempo sem vídeo passa de cerca de 3 s para cerca de 0,3 s. Não muda o desenho, por isso entra depois de o resto funcionar.

---

## 10. Reencaminhar o vídeo

### O pacote de vídeo (DATA)

O servidor de vídeo do professor envia cada frame num pacote RTP (um formato normal para vídeo, que já traz o número do frame). O nosso pacote DATA leva:

1. o nosso cabeçalho, com o fluxo (servidor, vídeo);
2. **8 bytes com a hora de envio**, em milissegundos, postos pelo nó do servidor;
3. o pacote RTP tal como veio.

A hora de envio serve para o cliente medir quanto tempo o frame demorou. A hora que vem dentro do RTP não serve, porque está em segundos inteiros. No CORE todos os nós partilham o relógio do computador, por isso a diferença entre a hora de chegada e a de envio é o atraso real.

O DATA não é confirmado nem reenviado: um frame atrasado já não serve.

### O que um nó faz com um DATA

1. **Só aceita o que vem do pai.** Um DATA de outro vizinho, ou de um vídeo que o nó não pediu, é deitado fora. Nesse caso o nó pode responder com um LEAVE, para o outro deixar logo de enviar.
2. Envia uma cópia a cada vizinho que está nos destinos.
3. Se o player local está nos destinos, tira os 8 bytes e entrega-lhe o pacote RTP.

### Nas pontas (sugestão, a combinar entre a Inês e o Pedro)

**No servidor.** O programa de vídeo do professor corre ao lado do nó e entrega-lhe os frames por UDP dentro da própria máquina. O nó só os envia para a rede se o vídeo tiver destinos; se não tiver, deita-os fora. Funciona como um canal de televisão em direto: quem entra apanha o vídeo no ponto onde ele vai.

```
python3 -m ott <ip-do-bootstrapper> --videos filme:6000 desenhos:6001
python3 player/Servidor.py movie.Mjpeg 127.0.0.1 6000
```

**No cliente.** O nó espera pelo anúncio que traz o vídeo com aquele nome, manda o JOIN e entrega os frames ao player.

```
python3 -m ott <ip-do-bootstrapper> --ver filme:25000
python3 player/Cliente.py 25000
```

Duas coisas a tratar no código do vídeo:

- O vídeo de teste tem 500 frames (25 segundos) e o programa para no fim. Tem de recomeçar, com o número do frame sempre a subir, porque o player deita fora frames com número mais baixo do que o último.
- Precisamos de pelo menos 2 vídeos diferentes por servidor, e só temos um.

---

## 11. Testar os caminhos (PING)

É a Etapa 3 do guião. O servidor envia de 5 em 5 segundos uma mensagem PING por cada vídeo que tem destinos. O PING segue exatamente o caminho do vídeo: cada nó envia-o aos destinos desse vídeo.

Cada nó por onde passa junta o seu número e a hora, e escreve uma linha no log. O cliente escreve o caminho completo:

```
PING 12 do vídeo (S1, 0): S1 → N1 (+0,6 ms) → N3 (+0,5 ms) → C1 (+0,6 ms)
```

(Os tempos deste exemplo são inventados.)

Serve só para testes e prints. Nenhum nó usa esta informação para escolher rotas.

---

## 12. As mensagens novas e os tempos

### Mensagens

| Código | Nome | Fiável? | Corpo |
|---|---|---|---|
| 0x10 | ANNOUNCE (anúncio) | Não | `{"srv": 1, "ronda": 57, "videos": {"0": "filme"}, "rtt": 2.0, "jitter": 0.1, "perda": 0.0, "saltos": 1}` |
| 0x11 | JOIN | O primeiro sim; as renovações não | Vazio. O vídeo vai no campo do fluxo do cabeçalho |
| 0x12 | LEAVE | Sim | Vazio. O vídeo vai no campo do fluxo |
| 0x13 | WITHDRAW | Sim | `{"srv": 1}` |
| 0x14 | PING | Não | `{"n": 12, "caminho": [{"id": 1, "t": 1731245000.123}]}`. O vídeo vai no campo do fluxo |
| 0x20 | DATA (já existe) | Não | 8 bytes com a hora de envio, seguidos do pacote RTP |

Os códigos de 0x10 a 0x1F já estavam reservados no protocolo para as rotas.

### Tempos (valores iniciais, a afinar nos testes)

| O quê | Valor |
|---|---|
| Período dos anúncios | 2 s (com variação ao acaso de 10%) |
| Validade do anúncio de um vizinho | 6 s |
| Renovação do JOIN | de 2 em 2 s |
| Validade de um destino sem renovação | 6 s |
| Margem para trocar de pai | mais de 15% e mais de 2 unidades de custo |
| Limite de saltos de um anúncio | 16 |
| PING | de 5 em 5 s |
| Sinal de vida entre vizinhos | de 1 em 1 s (hoje: 0,5 s) |
| Silêncio até dar um vizinho como em baixo | 3 sinais, ou seja 3 s (hoje: 3,5 s) |

---

## 13. O que falta decidir

- [ ] **Renovação do JOIN** (secção 8), em vez de rotas que ficam ativas até alguém as desligar.
- [ ] **A fórmula do custo** e os pesos (secção 6): 20 por cada 1% de perda e 1 por salto. A alternativa era uma ordem de prioridade: primeiro excluir ligações com perda acima de um limite, depois escolher pelo tempo. Tem o problema de a rota saltar quando a perda anda à volta do limite.
- [ ] **Como medir a perda.** A proposta é contar sinais de vida sem confirmação. Mede a ida e a volta juntas e só dá uma amostra por segundo. A alternativa melhor, se sobrar tempo, é cada nó numerar os pacotes que envia a cada vizinho, e o vizinho contar os números que faltam.
- [ ] **Os tempos do sinal de vida** (1 s e 3 sinais), e combiná-los entre vizinhos na mensagem de apresentação em vez de os ter fixos no código.
- [ ] **O formato do DATA** com os 8 bytes da hora de envio.
- [ ] **Não partir os frames** em pedaços. Perder um pedaço estraga o frame na mesma, por isso partir sozinho não melhora nada.
- [ ] **As opções da linha de comando** e as portas locais para o servidor e o cliente (secção 10).

---

## 14. Por onde começar

Para ter vídeo a passar o mais cedo possível, a ordem proposta é:

1. **Versão mínima.** Anúncios periódicos com custo. Cada nó fica com o melhor pai e só troca se o pai cair; nesse caso espera pela ronda seguinte. JOIN e LEAVE sem renovação. Reencaminhar o DATA. PING.
   *Pronto quando:* no cenário 2 um cliente vê o vídeo e o PING mostra o caminho.
2. **Limpeza automática.** Renovação do JOIN, anúncios que expiram, tratar o vizinho em baixo.
3. **Custo completo.** As quatro medidas e a troca com margem.
4. **Recuperação rápida.** O WITHDRAW e a reserva válida (regra 3 da secção 7).
5. **Deteção pela falta de vídeo.**

Cada passo deixa o sistema a funcionar. Se o tempo acabar a meio, o que está feito serve.

---

## 15. O que este desenho segue

O professor pediu que disséssemos no relatório o que seguimos, em vez de justificar tudo com testes nossos. Convém confirmar cada referência antes de a citar.

| No nosso desenho | De onde vem a ideia |
|---|---|
| Anúncios com número de ronda e custo somado em cada salto | Protocolos de vetor de distâncias com número de sequência: DSDV e Babel (RFC 8966) |
| Regra para nunca escolher quem recebe através de mim | Condição de viabilidade do Babel e do EIGRP |
| Passar a mensagem a todos os vizinhos sem repetir, e apagar o que não é renovado | Inundação do OSPF (RFC 2328): número de sequência e idade máxima |
| JOIN enviado salto a salto na direção da origem, renovado periodicamente | Multicast nos routers: PIM (RFC 7761) |
| Sinal de vida com intervalo combinado entre vizinhos, e limite igual a um número de sinais em falta | BFD (RFC 5880). O valor 3 é o habitual |
| Limite de silêncio nunca abaixo de 3 vezes o tempo de reenvio | Regra do QUIC para o tempo de inatividade (RFC 9000) |
| Cálculo do tempo de reenvio a partir do tempo de ida e volta | TCP (RFC 6298), já usado no nosso transporte |
| Um overlay que reage a falhas mais depressa do que a rede por baixo | *Resilient Overlay Networks* (Andersen e outros, 2001) |

---

## Glossário

| Termo | O que quer dizer |
|---|---|
| **Overlay** | A rede "virtual" que os nossos programas formam por cima da rede real. |
| **Nó** | Um dos nossos programas a correr numa máquina. Servidores e clientes também são nós. |
| **Vizinho** | Um nó com quem outro nó fala diretamente no overlay. |
| **Bootstrapper** | Programa que só recebe os nós novos e lhes diz o seu número e os vizinhos. |
| **Fluxo** | Um vídeo de um servidor, identificado pelo par (servidor, número do vídeo). |
| **Anúncio (ANNOUNCE)** | Mensagem que cada servidor espalha pela rede a dizer que existe e que vídeos tem. |
| **Ronda** | O número de um anúncio. Todos os anúncios que nascem do mesmo envio do servidor têm a mesma ronda. |
| **Custo** | Número que resume a qualidade de um caminho. Quanto mais baixo, melhor. |
| **Pai** | O vizinho de quem um nó recebe os vídeos de um servidor. No guião chama-se "Origem". |
| **Destinos** | Os vizinhos a quem um nó envia um vídeo. |
| **Reserva válida** | Um vizinho que pode substituir o pai sem risco de criar um ciclo. |
| **Ciclo** | Nós a mandarem o vídeo uns para os outros em círculo, sem chegar a ninguém. |
| **Margem** | Diferença mínima para valer a pena trocar de pai. Evita trocas constantes entre caminhos parecidos. |
| **JOIN / LEAVE** | Pedido para começar a receber um vídeo / aviso de que já não se quer. |
| **WITHDRAW** | Aviso de "perdi a rota para este servidor". |
| **PING** | Mensagem de teste que segue o caminho do vídeo e regista por onde passou. |
| **DATA** | O pacote que leva um frame de vídeo. |
| **Fiável / não fiável** | Uma mensagem fiável é confirmada e reenviada até chegar. Uma não fiável é enviada uma vez. |
| **Sinal de vida** | Mensagem pequena só para dizer "estou vivo". Também serve para medir a ligação. |
| **Tempo de ida e volta (RTT)** | Tempo desde que se envia um pacote até chegar a confirmação dele. |
| **Variação do atraso (jitter)** | Quanto o atraso muda de pacote para pacote. |
| **Perda** | Percentagem de pacotes que não chegam. |
| **Salto** | Cada passagem de um nó para o seguinte no overlay. |
| **Frame** | Uma imagem do vídeo. O vídeo de teste tem 20 por segundo. |
| **Fragmento** | Pedaço de um pacote que a rede partiu por ser grande demais. |
| **RTP** | Formato normal para enviar vídeo e áudio em tempo real. Cada pacote traz um número de sequência. |
| **Vetor de distâncias** | Família de protocolos em que cada nó só sabe o custo até cada destino e por que vizinho ir, sem conhecer o mapa. |
| **Multicast** | Envio de um para um grupo de interessados, com os nós intermédios a replicar. |
| **Babel, EIGRP, DSDV, OSPF** | Protocolos de encaminhamento usados em redes reais. Servem aqui de inspiração para algumas regras. |
| **PIM** | Protocolo com que os routers encaminham multicast. |
| **BFD** | Protocolo usado nos routers para detetar depressa que um vizinho deixou de responder. |
| **QUIC** | Protocolo moderno (o do HTTP/3) que dá garantias por cima de UDP. Foi a inspiração do nosso transporte. |
| **RFC** | Documento que especifica um protocolo da Internet. |
| **CORE** | O emulador de redes onde corremos os testes. |
