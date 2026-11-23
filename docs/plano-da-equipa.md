# TP2 ESR — Plano da equipa

Atualizado a 23/11/2026 · Grupo: George, Inês, Pedro

Este documento diz o que o trabalho pede, o que está feito, o que está decidido e o que falta, por ordem. Lê-se sozinho. O "como" de cada mecanismo está no `docs/protocolo.md` e no `docs/testes.md`; aqui só fica o resumo.

---

## 1. O que o guião pede, e onde estamos

| O guião pede | Estado |
|---|---|
| Um nó é um programa que se arranca à mão numa máquina | Feito: `python3 -m ott <ip-do-bootstrapper>` |
| Os nós podem entrar e sair a qualquer momento | Feito: aviso de saída (CLOSE), deteção pelo silêncio e nova ligação depois de uma falha |
| Cada nó mantém ligações com os vizinhos | Feito |
| Ao arrancar, o nó conhece um nó que lhe diz os vizinhos | Feito: o bootstrapper |
| Tabela de rotas por vídeo (origem, custo, destinos, ativa) | Quase: falta o custo na linha da tabela que o nó escreve no log |
| Topologia com caminhos alternativos e vários clientes | Feito: cenário 3, 13 nós, 2 servidores, 2 vizinhos por cliente |
| Rotas atualizadas quando entram e saem nós e quando a rede muda | Feito no código. Testado no CORE em parte (falta o cenário 2 e piorar ligações) |
| Os nós reencaminham o vídeo segundo a tabela | Feito, testado no CORE |
| Pelo menos 2 servidores, cada um com pelo menos 2 vídeos | Feito: os dois servidores têm os mesmos 2 vídeos |
| Rotas escolhidas com medidas da rede | Feito: RTT, variação do atraso, perda e saltos |

**Avaliação:**

| Item | Peso | Onde estamos |
|---|---|---|
| Topologia do overlay | 10% | Feito |
| Gestão dos fluxos de vídeo | 25% | Feito; faltam os testes finais com prints |
| Protocolo de controlo | 15% | Feito, com o HELLO único na `main` (22/11) |
| Protocolo de streaming | 10% | Feito, com medições no cliente |
| Solução final integrada | 30% | Testes a meio; relatório e defesa por começar |
| Avaliações intermédias | 5% + 5% | — |

Em cada item contam a ideia, a qualidade do código e a qualidade dos testes e do espírito crítico.

**Entrega:** relatório em formato de artigo (LNCS, máximo 12 páginas: introdução, arquitetura, especificação dos protocolos, implementação, testes e resultados, conclusões) e código num zip (`ESR-TP2-PLx-Gy-Rel.pdf` e `ESR-TP2-PLx-Gy-Codigo.zip`).

---

## 2. Regras do professor

- O **bootstrapper** só inicializa a rede (número e vizinhos de cada nó). Não faz mais nada.
- **Um nó só conhece os seus vizinhos.** A única exceção autorizada é o "avô" do pedido de socorro (ver extras).
- Inspirarmo-nos no **QUIC**, sem usar o QUIC a sério.
- O sinal de vida tem de ser **adaptativo e seguir algo real**, e o relatório diz o que seguimos, em vez de justificar os valores só com testes nossos.
- **Relatórios:** dizer o que cada teste mostra; cada número citado tem de estar num print, com uma caixa à volta; texto simples e curto.

---

## 3. O que está decidido

| Assunto | Decisão | O que segue / porquê |
|---|---|---|
| Linguagem | Python 3, só biblioteca standard no nó | Código base do professor em Python |
| Transporte | UDP com protocolo nosso: cabeçalho binário de 14 bytes, controlo em JSON, vídeo em bytes crus, uma porta (5000) para tudo | QUIC: nº de pacote nunca reutilizado, id da mensagem separado |
| Fiabilidade | Controlo confirmado e reenviado; vídeo não | Um frame atrasado já não serve |
| Bootstrapper | Só dá o número e os vizinhos, lidos de um JSON | Regra do professor |
| **Vizinhos: HELLO único** | Um só HELLO periódico faz de apresentação, sinal de vida e nova tentativa: 1 s com o vizinho vivo; 3 a 1 s e depois 5 s com quem não responde; nenhum para quem saiu com CLOSE. Leva o nº de arranque, o intervalo e os vizinhos ouvidos | Hello do OSPF e BFD. Em baixo ao fim de 3 intervalos (BFD), nunca abaixo de 3 × RTO (QUIC). Decidido a 17/11; na `main` a 22/11 (PR #13) |
| Rotas | Os servidores anunciam-se de 2 em 2 s; cada nó escolhe o pai pelo custo; os clientes pedem o vídeo (JOIN) pelo caminho inverso, com renovação de 2 em 2 s | Vetor de distâncias com nº de sequência (DSDV, Babel); JOIN como no PIM |
| Custo | Hoje: RTT mínimo + variação do atraso + 20 × perda (%) + 1 por salto; troca por um pai melhor só com mais de 15% e 2 unidades de diferença. **A trocar** por uma métrica ao estilo ETX/ETT (decidido a 22/11), porque o peso 20 da perda não tinha fundamento | Um custo único que cresce a cada salto, para a regra anti-ciclo |
| Anti-ciclo | Só pode ser pai um vizinho com ronda mais nova, ou a mesma ronda com custo anunciado menor | Condição de viabilidade do Babel e do EIGRP |
| Falhas | WITHDRAW quando um nó fica sem rota; anúncios valem 6 s; 300 ms sem vídeo do pai fazem trocar de pai | No CORE: troca em 0,34–0,50 s depois de um nó morrer, contra 3,5 s pelo silêncio |
| Vídeos | 2 servidores com os mesmos 2 vídeos; o cliente pede o vídeo k ao servidor mais barato e troca de servidor se esse ficar sem rota | Decidido em grupo |
| Frames | Não se partem em pedaços (perder um pedaço estraga o frame na mesma); testes com perda de 1 a 5% | — |
| Extra principal | A rede recupera sozinha quando algo falha ou piora, e medimos quanto tempo leva | — |
| Git | Um ramo por pessoa, junto à `main` quando estiver testado | — |

---

## 4. Onde estamos (23/11)

**Feito e na `main`:** transporte, bootstrapper, vizinhos com o HELLO único, rotas (anúncios, custo, margem, anti-ciclo, expiração, WITHDRAW), deteção pela falta de vídeo, servidor mais perto e troca de servidor, 2 vídeos por servidor (também no arranque do cenário 3), medições no cliente (`Medidor.py`, `analisar_medicoes.py`, `debito_interface.py`, modo `--medir` do arranque), ferramentas de teste (painel, `no.sh`, `testar_hello.sh`, logs com o nome do nó). 70 testes automáticos.

**Limpeza de 23/11:** saíram a demo antiga do transporte e os scripts do keepalive de 0,5 s; as topologias passaram a `cenario1.xml` a `cenario3.xml`; os documentos foram revistos (o desenho das rotas de 14/11 passou para o `protocolo.md`, e o `extras.md` só tem o estado de cada extra).

**Testado no CORE (cenário 3):**
- arranque, caminhos e cópia única (o N2 serve o C2 e o C3 com uma só cópia);
- um nó intermédio a sair com aviso e a morrer sem aviso (o vídeo parou 0,35 a 0,55 s);
- os 4 testes do HELLO único (17/11): cortar e repor a ligação C1–N1 (o C1 troca para o N3 em 0,35 s; voltam a ligar-se sozinhos em 3,0 s), parar e arrancar o N1, matar e arrancar o N1;
- com e sem overlay, com 1 a 3 clientes: com 3 clientes, o servidor envia 35% menos com overlay; o preço são 3 a 5 ms de atraso e um arranque mais lento (6 a 9 s, contra 2 s). Os números estão no `docs/resultados-medicoes.md`.

**Problemas conhecidos:**
- A troca de servidor congela o vídeo cerca de 2 s (os dois servidores têm o nº do frame desfasado).
- A perda é medida com poucos HELLOs: com 1 a 2% de perda, as rotas podem trocar de pai sem necessidade.
- O peso da perda no custo (20) não tem fundamento: vai ser trocado por uma métrica ao estilo ETX/ETT.
- Falta o custo na linha da tabela de fluxos que o nó escreve no log.
- O arranque do cliente com overlay demora 6 a 9 s (inclui esperar duas rondas de anúncios antes de escolher o servidor).

---

## 5. Ordem de trabalho até à entrega

| Semana | Objetivo |
|---|---|
| **16 a 22/11** | Testes no CORE com prints, decisão do HELLO único (feita), reunião dos extras, correções pequenas (custo na tabela, estabilidade da perda) |
| **23 a 29/11** | Checkpoint 2. Fechar o código (rotas estáveis com pouca perda, custo ETX/ETT, congelamento na troca de servidor, extras escolhidos). Bateria completa de testes com números. Esqueleto do relatório e primeiras secções |
| **30/11 a 6/12** | Repetir as medições com overlay na versão final do código. Extras que sobrarem. Relatório: todas as secções em rascunho |
| **7 a 13/12** | Relatório completo, revisto por todos. Prints finais |
| **14/12 até à entrega** | Revisão final, zip do código, preparação da defesa |

Princípio: um extra só entra se o básico estiver testado; se o tempo acabar, fica de fora um extra, não o básico.

---

## 6. Tarefas de cada um

### Esta semana (23 a 29/11)

O que ficou por fazer de 16 a 22/11 passa para o início desta.

**George**
- [ ] Rotas estáveis com pouca perda: uma ligação com 1 a 2% de perda não põe o pai a trocar
- [ ] Custo na linha da tabela de fluxos
- [ ] Trocar a fórmula do custo por uma métrica ao estilo ETX/ETT
- [ ] Testes no CORE com prints: piorar uma ligação, parar um servidor, o cenário 2
- [ ] Relatório: secção dos protocolos (a partir do `docs/protocolo.md`)

**Inês**
- [ ] Testes dos vizinhos e fluxos no CORE, com prints: um nó sai e volta; o último cliente desiste e o tráfego para; um nó entra com o vídeo a passar
- [ ] Extras escolhidos na reunião que lhe couberem (por exemplo, o avô)
- [ ] Relatório: secção da arquitetura

**Pedro**
- [ ] Acabar com o congelamento na troca de servidor
- [ ] Bateria de medições: com e sem overlay (1, 2 e 3 clientes); nó que morre, ligação cortada, ligação que piora; servidor que cai
- [ ] Confirmar no CORE a ligação R5–R7 e o IP do S1 na tabela de endereços
- [ ] Relatório: secção dos testes e resultados

**Todos**
- [ ] Reunião dos extras
- [ ] Esqueleto do relatório (LNCS, máximo 12 páginas) e prints organizados em `relatorio/evidencias/`

---

## 7. Decisões em aberto

1. **Que extras entram** (reunião desta semana). Candidatos, explicados no `docs/extras.md`: o pedido de socorro ao "avô" (autorizado pelo professor); o desenho da árvore a partir dos logs; o leitor do protocolo para o Wireshark; a comparação com o OSPF dos routers; medir a perda pelos números em falta; o teste com a largura de banda de uma ligação limitada (mostra a cópia única com números).
2. **Como estabilizar a medida da perda:** uma janela maior ou suavizada, ou não voltar logo a um pai que se acabou de largar (como o *route flap damping* do BGP).
3. **Encurtar o arranque do cliente** (6 a 9 s)? Por exemplo, escolher logo o servidor e trocar depois pela margem, em vez de esperar duas rondas. Só se não trouxer de volta o problema de ficar preso ao primeiro servidor.

---

## 8. Testes que o relatório precisa

| Teste | Estado |
|---|---|
| Os nós arrancam e formam o overlay (cenários 2 e 3) | Cenário 3 feito; cenário 2 por fazer |
| Um cliente pede um vídeo e o PING mostra o caminho | Feito (cenário 3) |
| Vários clientes no mesmo vídeo: tráfego à saída do servidor com e sem overlay | Feito (1 a 3 clientes); repetir na versão final |
| O último cliente desiste e o tráfego para | Por fazer |
| Um nó intermédio sai com aviso / morre sem aviso | Feito (cenário 3, também com o HELLO único); repetir com medições |
| Uma ligação é cortada e reposta | Feito (HELLO único, 17/11) |
| Uma ligação piora (atraso ou 1 a 5% de perda): a rota muda e não fica a saltar | Por fazer |
| Um servidor cai e os clientes passam para o outro | Feito em parte; repetir depois de corrigir o congelamento |
| Dois servidores com dois vídeos cada, ao mesmo tempo | Por fazer |
| Um nó entra com o vídeo a passar | Por fazer |

---

## 9. Dúvidas para o professor

1. **Aceita as medidas que escolhemos** (RTT, saltos, perda e variação do atraso, medidas pelo nosso protocolo)?
2. **Aceita os extras** que escolhermos?
3. **HELLO único:** ficou combinado desistir ao fim de cerca de 3 HELLOs sem resposta; no desenho final, o nó abranda para 5 s em vez de parar (como o BFD), para recuperar de falhas de rede entre nós vivos. Está bem assim?

Já respondidas: são 2 servidores; a exceção do "avô" está autorizada.

---

## 10. Regras de trabalho

- **Git:** um ramo por pessoa, junto à `main` quando estiver testado. Antes de continuar a trabalhar, juntar a `main` ao ramo.
- **Testes no CORE:** `sudo -E bash scripts/arrancar_cenario.sh 3` arranca tudo e abre os players e o painel; `sudo bash scripts/no.sh ...` para parar, matar ou arrancar nós e piorar, cortar ou repor ligações; `parar-tudo` antes do Stop do CORE. Detalhes no `docs/testes.md`.
- **Não vão para o git:** capturas e logs (a pasta `logs/`). Os resultados que entram no relatório ficam em `docs/medicoes/` e os prints em `relatorio/evidencias/`.
- **Prints:** nomes do tipo `E2_NN_<o-que-mostra>.png`, com uma caixa à volta dos valores que o texto usa.
- **Termos técnicos:** quem usar um termo novo explica-o no próprio documento.

---

## Glossário

| Termo | O que quer dizer |
|---|---|
| **Overlay / underlay** | A rede "virtual" dos nossos programas / a rede real (routers e ligações) por baixo. |
| **Nó, vizinho** | Um dos nossos programas; um nó com quem fala diretamente no overlay. |
| **Bootstrapper** | Programa que só diz a cada nó novo o seu número e os vizinhos. |
| **Fluxo** | Um vídeo de um servidor: o par (servidor, número do vídeo). |
| **Anúncio, ronda** | Mensagem periódica de cada servidor que espalha o custo até ele; a ronda é o número de cada envio. |
| **Pai** | O vizinho de quem um nó recebe os vídeos de um servidor. |
| **JOIN / LEAVE / WITHDRAW** | Pedir um vídeo / desistir dele / avisar que se perdeu a rota para um servidor. |
| **HELLO / CLOSE** | Mensagem periódica entre vizinhos (apresentação e sinal de vida) / aviso de saída. |
| **RTT, jitter, perda** | Tempo de ida e volta; quanto ele varia; percentagem de pacotes que não chegam. |
| **RTO** | Tempo de espera antes de reenviar uma mensagem sem confirmação, calculado a partir do RTT. |
| **Margem** | Diferença mínima de custo para valer a pena trocar de pai. |
| **Cópia única** | Na parte do caminho partilhada por vários clientes, passa uma só cópia do vídeo. |
| **ETX / ETT** | Métricas de custo de redes sem fios: o número esperado de envios para um pacote passar / o tempo que isso leva. |
| **QUIC, BFD, OSPF, Babel, EIGRP, PIM, DSDV, BGP** | Protocolos reais em que nos inspirámos para algumas regras. |
| **CORE, tc** | O emulador de redes; o comando do Linux que simula perda e atraso. |
| **LNCS** | Modelo de artigo da Springer, recomendado para o relatório. |
