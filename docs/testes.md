# Guia de testes no CORE

Como meter perda num link e como parar e reiniciar um nó. Os nomes e interfaces são os do cenário 2 (`topologias/testte2.xml`); os IPs estão em [`enderecos.md`](enderecos.md).

Todos os comandos correm na shell do nó (duplo clique no nó no CORE). Cada nó tem a sua própria rede, por isso um `tc` só mexe nas interfaces desse nó.

## 1. Perda com `tc`

O diálogo de link do CORE não aplicou a perda no nosso caso, por isso usamos o `tc netem` diretamente.

### Interfaces

| Nó | Interface | Liga a |
|---|---|---|
| S1, N1, N2, N3, C1, C2 | eth0 | o seu router |
| n7 | eth0 / eth1 / eth2 / eth3 | S1 / N1 / N2 / n8 |
| n8 | eth0 / eth1 / eth2 / eth3 | N3 / C1 / C2 / n7 |

### Perda em toda a interface

```bash
tc qdisc add dev eth0 root netem loss 20%      # pôr 20 % de perda
tc qdisc change dev eth0 root netem loss 40%   # mudar o valor
tc qdisc show dev eth0                         # ver o que está aplicado
tc -s qdisc show dev eth0                      # o mesmo, com contagem de pacotes descartados
tc qdisc del dev eth0 root                     # tirar a perda
```

Notas:

- O `netem` só atua nos pacotes que **saem** pela interface. Para perda nos dois sentidos, aplicar nas duas pontas (ex: `eth0` do C2 e `eth2` do n8).
- Num PC, a `eth0` leva todas as ligações overlay desse nó. Ex: perda na `eth0` do N2 afeta N2↔N1, N2↔N3 e N2↔C2 ao mesmo tempo.
- Se o `add` der `File exists`, já há um qdisc: usar `change`, ou `del` primeiro.
- Também dá para meter atraso: `netem delay 50ms` ou `netem loss 20% delay 50ms`.

### Perda só para um vizinho overlay

Para estragar uma única ligação overlay (ex: N2→N3) sem mexer nas outras, filtra-se pelo IP de destino. No N2:

```bash
tc qdisc add dev eth0 root handle 1: prio
tc qdisc add dev eth0 parent 1:3 handle 30: netem loss 20%
tc filter add dev eth0 parent 1:0 protocol ip u32 match ip dst 10.0.3.20/32 flowid 1:3
```

Só os pacotes para 10.0.3.20 (N3) passam pelo `netem`; o resto segue normal. Para tirar: `tc qdisc del dev eth0 root`.

### Confirmar que a perda está ativa

```bash
ping -c 100 -i 0.2 -q 10.0.5.20
```

A linha `packet loss` deve andar perto do valor configurado (com perda nas duas pontas soma-se: 20 % + 20 % ≈ 36 %).

## 2. Parar e reiniciar um nó

Serve para testar falhas: parar um nó overlay (ex: N2) e ver se o C2 passa a receber por outro caminho.

### Parar

- No terminal onde o nó está a correr: `Ctrl+C`.
- Noutro terminal do mesmo nó, pelo porto UDP (5000 na demo):

  ```bash
  ss -Hulpn 'sport = :5000'        # mostra o pid de quem usa o porto
  kill <pid>                       # parar
  kill -9 <pid>                    # se não sair (ex: ficou suspenso com Ctrl+Z)
  ```

**Não usar `pkill -f python3` nem `killall`.** No CORE os nós só têm a rede separada; os processos são todos visíveis entre nós, por isso isto mata os programas de **todos** os nós. O `ss` é seguro porque só vê os sockets do nó onde corre.

### Reiniciar

Voltar a correr o mesmo comando no terminal do nó. Por agora, com a demo do transporte:

```bash
bash scripts/demo-cenario1.sh            # (topologia teste1) já para uma demo antiga no porto 5000
```

Quando o nó `ott` estiver pronto:

```bash
python3 -m ott <ip-do-bootstrapper>
```

### Cortar o link sem matar o processo

Para simular um cabo cortado (o processo continua vivo, mas deixa de receber e enviar):

```bash
ip link set eth0 down
ip link set eth0 up
```

## 3. Antes de cada teste

1. `tc qdisc show dev eth0` nos nós envolvidos: confirmar que não ficou perda de um teste anterior.
2. `ss -Hulpn 'sport = :5000'`: confirmar que não ficou um processo antigo a ocupar o porto.
3. Registar no log o cenário, o nó e a perda aplicada (ex: `bash scripts/demo-cenario1.sh perda20` grava em `logs/<nó>-perda20.log`).
