# Guia de testes no CORE

Como arrancar um cenário, mexer nos nós e nas ligações durante um teste, e medir. Os IPs de cada
cenário estão em [`enderecos.md`](enderecos.md).

| Cenário | Topologia | Config do bootstrapper |
|---|---|---|
| 1 (o do enunciado: S1, O1, C1, C2) | `topologias/cenario1.xml` | `configs/cenario1.json` |
| 2 (6 PCs, 2 routers) | `topologias/cenario2.xml` | `configs/cenario2.json` |
| 3 (13 nós, 2 servidores; o principal) | `topologias/cenario3.xml` | `configs/cenario3.json` |

## 1. Arrancar um cenário inteiro com um só comando

Depois de abrir a topologia no CORE e carregar em Start, na máquina (fora do CORE):

```bash
xhost +SI:localuser:root      # uma vez por sessão gráfica
./rede start 3                # ou 2; é o mesmo que: sudo -E bash scripts/arrancar_cenario.sh 3
```

O script espera que os routers já encaminhem e arranca o bootstrapper, os nós, os players e por fim os servidores (para o 1.º anúncio já encontrar as ligações feitas). Os programas correm sem janela: cada um escreve em `logs/cenarioN-<nó>[-<programa>].log`, com o nome do nó em cada linha. Abrem-se só as janelas dos players e um painel. Cada player tem no topo uma faixa com o nome do cliente (C1, C2, ...), cada um na sua cor, e abrem lado a lado. Com `--janelas` no fim, abre uma janela por programa.

No cenário 3, os dois servidores têm os mesmos 2 vídeos e os clientes pedem o vídeo 0 (`--ver 0`): cada um fica com o servidor mais barato. O bootstrapper corre no PC do N4 (10.0.21.20) na porta 5001, para não chocar com o nó do N4 na porta 5000.

**Painel** (`./rede painel`, aberto pelo script): lê os logs de 2 em 2 s e mostra, com nomes e cores:
- **por onde passa o vídeo**: a árvore de cada vídeo, do servidor aos clientes (▶ = cliente a ver; ✖ = nó em baixo);
- **clientes**: o vídeo que cada um está a ver, o caminho e o atraso do último PING, os frames em falta e a última paragem do player;
- **nós**: estado (● ativo, ◌ a arrancar, ✖ em baixo, ■ parado), o pai para cada servidor e os vizinhos ativos;
- **últimos acontecimentos**: falhas a vermelho, recuperações a verde, as nossas ações (`./rede ...`) a ciano.

A barra de cima fica vermelha enquanto houver um nó em baixo. `./rede status` mostra o painel uma vez (bom para prints), e `python3 scripts/painel.py 3 --hora 16:05:30` mostra como estava a rede a essa hora. Só lê ficheiros: nenhum nó fica a conhecer a árvore.

**Mexer num nó ou numa ligação** a partir do terminal normal, com o `./rede` (pede o sudo sozinho; cada ação fica marcada nos logs e aparece no painel):

```bash
./rede kill N1            # kill -9: falha sem aviso (também: ./rede kill N1 N2)
./rede down N1            # Ctrl+C: sai e avisa os vizinhos (CLOSE)
./rede up N1              # volta a arrancar, com o comando do arranque
./rede restart N1         # Ctrl+C e arranca de novo (restart N1 -9: com kill -9)
./rede loss C1 N1 2%      # perda nos dois sentidos da ligação C1–N1
./rede delay C1 N1 50ms   # atraso (delay C1 N1 50ms 10ms: com variação)
./rede cut C1 N1          # 100% de perda entre C1 e N1
./rede fix C1 N1          # tira o que o loss/delay/cut pôs nessa ligação
./rede run C1 tc -s qdisc show
./rede logs N1            # segue o log do N1
./rede stop               # no fim, antes do Stop do CORE
./rede help               # todos os comandos
```

Para o player, um servidor de vídeo ou o bootstrapper, junta o programa depois do nó: `./rede kill S1 video0`, `./rede up C1 player`. O `./rede` chama o `scripts/no.sh`, que também se pode usar diretamente (`sudo bash scripts/no.sh matar N1`, ...).

Os programas correm em segundo plano, por isso o Stop do CORE não chega para os parar: usar o `./rede stop` antes. O script de arranque também para o que tiver ficado de uma execução anterior.

**Desenho da árvore** (`./rede arvore`): escreve a árvore de cada vídeo no terminal e faz uma figura em `logs/arvore-cenarioN.svg`, com as posições do CORE, os vizinhos do overlay a tracejado e uma seta de cor por vídeo. Para o relatório:

```bash
python3 scripts/arvore.py 3 --hora 16:05:30 --saida relatorio/figuras/arvore-antes.svg   # estado a essa hora
python3 scripts/arvore.py 3 --rede-real          # com os routers e os cabos da rede real, a cinzento
python3 scripts/arvore.py 3 --seguir             # refaz a figura de 2 em 2 s; abrir logs/arvore-cenario3.html no browser
```

Tal como o painel, só lê os logs.

**Testes do HELLO** (cenário 3): `./rede hello` arranca o cenário e faz, seguidos, os 4 testes na ligação C1–N1: arranque, cortar e repor, parar e arrancar, matar e arrancar. Guarda o painel antes e depois de cada passo em `logs/teste-hello/`.

## 2. Arrancar à mão

Na pasta do projeto, numa shell de cada nó (duplo clique no nó no CORE). Os nós usam um IP do bootstrapper alcançável a partir deles.

**Cenário 2.** O bootstrapper corre no router n7; os nós usam o IP do n7 do lado deles (`10.0.0.1` no S1, `10.0.1.1` no N1, `10.0.2.1` no N2) e os ligados ao n8 usam `10.0.6.1`. O nó do S1 arranca por último.

```
n7:  python3 -m bootstrapper configs/cenario2.json
N1:  python3 -m ott 10.0.1.1
N2:  python3 -m ott 10.0.2.1
N3:  python3 -m ott 10.0.6.1
C1:  python3 player/Cliente.py                       # e noutro terminal:
C1:  python3 -m ott 10.0.6.1 --player --ver 1:0
C2:  (igual ao C1)
S1:  python3 player/Servidor.py videos/movie.Mjpeg   # e noutro terminal:
S1:  python3 -m ott 10.0.0.1 --servidor
```

Pais esperados (`pai para o servidor 1: <vizinho> (custo X)` no log): N1 → 1, N2 → 2, N3 → 2, C1 → 3, C2 → 4.

**Cenário 3.** O bootstrapper corre no PC do N4, na porta 5001:

```
N4:      sed 's/"port": 5000/"port": 5001/' configs/cenario3.json > /tmp/cenario3-bootstrapper.json
N4:      python3 -m bootstrapper /tmp/cenario3-bootstrapper.json
N1 a N6: python3 -m ott 10.0.21.20 --porta 5001
C1 a C5: python3 player/Cliente.py                                         # e noutro terminal:
C1 a C5: python3 -m ott 10.0.21.20 --porta 5001 --player --ver 0
S1, S2:  python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000     # vídeo 0
S1, S2:  python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001    # vídeo 1
S1, S2:  python3 -m ott 10.0.21.20 --porta 5001 --servidor --servidor
```

No log de cada cliente aparece `vídeo 0: servidor 17 (custo X)` (S1) ou `servidor 16` (S2). Quando um servidor para, os clientes dele mostram `vídeo 0: servidor <outro> (custo X), antes <o que parou>`.

## 3. Perda e atraso com `tc`, à mão

O `no.sh piorar` faz isto por nós. À mão, na shell do nó: cada nó do CORE tem a sua própria rede, por isso um `tc` só mexe nas interfaces desse nó. O diálogo de ligação do CORE não aplicou a perda no nosso caso, por isso usamos o `tc netem` diretamente.

```bash
tc qdisc add dev eth0 root netem loss 20%      # pôr 20 % de perda
tc qdisc change dev eth0 root netem loss 40%   # mudar o valor
tc qdisc show dev eth0                         # ver o que está aplicado
tc -s qdisc show dev eth0                      # o mesmo, com contagem de pacotes descartados
tc qdisc del dev eth0 root                     # tirar a perda
```

- O `netem` só atua nos pacotes que **saem** pela interface. Para perda nos dois sentidos, aplicar nas duas pontas.
- Num PC, a `eth0` leva todas as ligações overlay desse nó: perda na `eth0` do N2 afeta todos os vizinhos do N2 ao mesmo tempo.
- Se o `add` der `File exists`, já há um qdisc: usar `change`, ou `del` primeiro.
- Também dá para meter atraso: `netem delay 50ms` ou `netem loss 20% delay 50ms`.

**Perda só para um vizinho.** Filtra-se pelo IP de destino (ex: no N2, só para o N3, 10.0.3.20, no cenário 2):

```bash
tc qdisc add dev eth0 root handle 1: prio
tc qdisc add dev eth0 parent 1:3 handle 30: netem loss 20%
tc filter add dev eth0 parent 1:0 protocol ip u32 match ip dst 10.0.3.20/32 flowid 1:3
```

**Confirmar** com `ping -c 100 -i 0.2 -q <ip>`: a linha `packet loss` deve andar perto do valor configurado (com perda nas duas pontas, 20 % + 20 % dá ≈ 36 %).

## 4. Parar, reiniciar e cortar à mão

- **Parar:** `Ctrl+C` no terminal do nó, ou noutro terminal do mesmo nó:

  ```bash
  ss -Hulpn 'sport = :5000'        # mostra o pid de quem usa a porta do nó
  kill <pid>                       # parar (sai com CLOSE)
  kill -9 <pid>                    # falha sem aviso
  ```

  **Não usar `pkill -f python3` nem `killall`.** No CORE os nós só têm a rede separada; os processos são todos visíveis entre nós, por isso isto mata os programas de **todos** os nós. O `ss` é seguro porque só vê os sockets do nó onde corre.
- **Reiniciar:** voltar a correr o mesmo comando no terminal do nó.
- **Cortar a ligação sem matar o processo:** `ip link set eth0 down` e, para repor, `ip link set eth0 up`.

**Antes de cada teste:** `tc qdisc show dev eth0` nos nós envolvidos (não ficou perda de um teste anterior) e `ss -Hulpn 'sport = :5000'` (não ficou um processo antigo a ocupar a porta).

## 5. Medições no cliente e comparação sem overlay

O `Servidor.py` põe no timestamp RTP a hora de envio de cada frame, em milissegundos. No CORE
todos os nós partilham o relógio do PC, por isso a hora de chegada menos a hora de envio é o atraso
de ponta a ponta. Em localhost dá perto de 0, por isso os números para o relatório vêm do CORE.

| Programa | Onde corre | O que faz |
|---|---|---|
| `player/Medidor.py <csv>` | cliente, no lugar do player (porta 25000) | grava uma linha por frame: chegada, nº, envio, atraso, tamanho |
| `scripts/analisar_medicoes.py <csv>…` | em qualquer lado, depois | perda, atraso (médio, mediana, p95, máx.), jitter (RFC 3550), débito, 1.º frame e cortes |
| `scripts/debito_interface.py eth0` | servidor | kbit/s e pacotes/s que saem e entram na interface, a cada segundo e em média |

O medidor e o player usam a mesma porta: num cliente corre um ou outro. O CSV é escrito linha a
linha, por isso pode ficar em `logs/` e parar-se com Ctrl+C ou com o Stop do CORE.

### Com o script (recomendado)

O `scripts/arrancar_cenario.sh` tem um modo de medição: arranca o que o teste precisa, com o
medidor no lugar do player, mede durante `--duracao` segundos (60 por omissão) depois de todos os
clientes arrancarem, para os medidores e mostra a análise.

```bash
sudo -E bash scripts/arrancar_cenario.sh 3 --medir overlay --clientes 1
sudo -E bash scripts/arrancar_cenario.sh 3 --medir direto --clientes 1
# ... repetir com --clientes 2 e 3 (entre testes: sudo bash scripts/no.sh parar-tudo, Stop e Start)
```

- `--medir overlay`: bootstrapper, nós intermédios, o S1 com o vídeo 0 (no cenário 3, sem o S2),
  e só depois os K clientes, cada um com o medidor e o nó OTT.
- `--medir direto`: nenhum nó OTT; o S1 corre um `Servidor.py` por cliente, direto para o IP dele.
- `--clientes K`: entram C1, …, CK (cenário 2: até 2; cenário 3: até 5). Por omissão, todos.

Fica tudo em `logs/medicoes/cenarioN-<modo>-<K>cli/`: um CSV por cliente, `S1-debito.log`
(débito da `eth0` do S1), `analise.txt` e uma cópia dos logs de todos os programas, tirada no
fim da medição. Se a pasta já existir, a antiga muda de nome (com a data e a hora). Para comparar
dois testes:

```bash
python3 scripts/analisar_medicoes.py logs/medicoes/cenario3-overlay-2cli/C1.csv logs/medicoes/cenario3-direto-2cli/C1.csv
```

Os testes de falhas fazem-se com o modo overlay e um `--duracao` maior, mexendo na ligação
durante a medição (ex: `sudo bash scripts/no.sh cortar C1 N1`). Os resultados que entram no
relatório copiam-se para `docs/medicoes/` (os de agora estão em
[`resultados-medicoes.md`](resultados-medicoes.md)).

### À mão

Com overlay, arrancar o cenário como na secção 2, mas nos clientes trocar o player pelo medidor;
sem overlay, nenhum nó OTT: o S1 corre um `Servidor.py` por cliente, direto para o IP dele.
Exemplo no cenário 2:

```bash
# com overlay
C1:  python3 player/Medidor.py logs/C1-overlay.csv --duracao 60
C1:  python3 -m ott 10.0.6.1 --player --ver 1:0
S1:  python3 scripts/debito_interface.py eth0 --duracao 60
# sem overlay
C1:  python3 player/Medidor.py logs/C1-direto.csv --ip 0.0.0.0 --duracao 60
S1:  python3 player/Servidor.py videos/movie.Mjpeg 10.0.4.20 25000     # para o C1
S1:  python3 scripts/debito_interface.py eth0 --duracao 60
# depois
python3 scripts/analisar_medicoes.py logs/C1-overlay.csv logs/C1-direto.csv
```

### O que se espera de cada teste

| Teste | O que se espera |
|---|---|
| 1, 2 e 3 clientes, com e sem overlay | Sem overlay, o tráfego à saída do S1 cresce com os clientes. Com overlay, sai uma só cópia por vídeo |
| Perda ou atraso numa ligação | A perda e o atraso no cliente sobem; com a troca de pai, voltam a descer |
| Cortar a ligação do pai | Um corte na análise (`aos X s: Y ms sem vídeo`): Y é o tempo de recuperação |
