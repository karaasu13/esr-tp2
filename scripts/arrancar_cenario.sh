#!/usr/bin/env bash
# Arranca um cenário inteiro no CORE com um só comando: bootstrapper, nós, players e servidores.
#
# Uso (na máquina, fora do CORE, depois de abrir a topologia e carregar em Start):
#   xhost +SI:localuser:root                        # uma vez por sessão gráfica
#   sudo -E bash scripts/arrancar_cenario.sh 3      # cenário 3 (topologias/cenario3.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 2      # cenário 2 (topologias/cenario2.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 3 --janelas   # uma janela por programa, como antes
#
# Os programas correm sem janela e o que escrevem fica em logs/cenarioN-<nó>[-<programa>].log,
# com o nome do nó em cada linha. Abrem-se só as janelas dos players (com o nome do nó no título)
# e um painel com o estado de todos os nós (scripts/painel.py).
# Para mexer num nó (parar, matar, piorar ou cortar uma ligação): scripts/no.sh.
# Para parar tudo: sudo bash scripts/no.sh parar-tudo, e depois o Stop do CORE.
#
# Modo de medição (docs/testes.md, secção 4): em vez dos players, os clientes correm o
# player/Medidor.py, o S1 mede o tráfego da eth0, e no fim o script para os medidores e mostra a
# análise. Tudo fica em logs/medicoes/cenarioN-<modo>-<K>cli/ (C1.csv, ..., S1-debito.log,
# analise.txt e uma cópia dos logs dos nós). Entre dois testes: no.sh parar-tudo, Stop e Start.
#   sudo -E bash scripts/arrancar_cenario.sh 3 --medir overlay --clientes 2   # com overlay
#   sudo -E bash scripts/arrancar_cenario.sh 3 --medir direto --clientes 2    # sem overlay
#   ... --duracao 90     # segundos de medição depois de todos os clientes arrancarem (60)
# Com --clientes K entram os K primeiros clientes (C1, ..., CK); por omissão, todos.
# Sem overlay não há nós OTT: o S1 corre um Servidor.py por cliente, a enviar direto para ele.
# Com overlay, no cenário 3 só arranca o S1 (com o vídeo 0), para comparar com o envio direto.
# O --janelas também funciona com --medir.
#
# Ordem: bootstrapper, nós intermédios, clientes e players, e os servidores por último, para o
# 1.º anúncio já encontrar as ligações feitas. No cenário 3 há dois servidores com os mesmos
# 2 vídeos e os clientes pedem o vídeo 0 (--ver 0): cada um fica com o servidor mais barato.
# Na medição com overlay os servidores arrancam antes dos clientes, para o "1.º frame" da análise
# ser o tempo que um cliente novo demora a receber vídeo, e não a espera pelo servidor.
set -euo pipefail

uso() {
  echo "Uso: sudo -E bash $0 2|3 [--janelas] [--medir overlay|direto] [--clientes K] [--duracao S]" >&2
  exit 1
}

CENARIO="${1:-}"
[ $# -gt 0 ] && shift
JANELAS=0
MODO=normal
K=""
DURACAO=60
while [ $# -gt 0 ]; do
  case "$1" in
    --janelas) JANELAS=1; shift; continue ;;
    --medir | --clientes | --duracao) [ $# -ge 2 ] || uso ;;
    *) uso ;;
  esac
  case "$1" in
    --medir) MODO="$2" ;;
    --clientes) K="$2" ;;
    --duracao) DURACAO="$2" ;;
  esac
  shift 2
done
case "$MODO" in normal | overlay | direto) ;; *) echo "--medir: overlay ou direto" >&2; uso ;; esac
[[ "$DURACAO" =~ ^[1-9][0-9]*$ ]] || { echo "--duracao: um número inteiro de segundos" >&2; uso; }
[ "$MODO" != normal ] || [ -z "$K" ] || { echo "--clientes só faz sentido com --medir" >&2; uso; }

# shellcheck source=comum.sh
source "$(dirname "$0")/comum.sh"
ESPERA=2         # segundos entre programas, para os vizinhos se apresentarem
ESPERA_VIDEO=5   # medição: segundos entre o último cliente e o início da medida no S1

# --- O que muda de cenário para cenário ---------------------------------------------------------
# REDE_NO/REDE_IP: um par que só se alcança quando o encaminhamento dos routers está pronto.
# CLIENTES e IP_CLIENTE: os clientes, pela ordem em que entram com --clientes K.
# OTT_CLIENTE: o nó OTT de um cliente. arrancar_base: bootstrapper e nós intermédios.
# arrancar_servidores: os servidores do arranque normal; servidor_medicao: só o S1, vídeo 0.
declare -A IP_CLIENTE
case "$CENARIO" in
2)
  REDE_NO=S1 REDE_IP=10.0.4.20
  CLIENTES=(C1 C2)
  IP_CLIENTE=([C1]=10.0.4.20 [C2]=10.0.5.20)
  OTT_CLIENTE="python3 -m ott 10.0.6.1 --player --ver 1:0"
  arrancar_base() {
    # Bootstrapper no router n7; cada nó usa o endereço do n7 virado para o seu lado.
    no n7 "python3 -m bootstrapper configs/cenario2.json" bootstrapper
    no N1 "python3 -m ott 10.0.1.1"
    no N2 "python3 -m ott 10.0.2.1"
    no N3 "python3 -m ott 10.0.6.1"
  }
  arrancar_servidores() {
    no S1 "python3 player/Servidor.py videos/movie.Mjpeg" video
    no S1 "python3 -m ott 10.0.0.1 --servidor"
  }
  servidor_medicao() { arrancar_servidores; }
  ;;
3)
  REDE_NO=S1 REDE_IP=10.0.26.20
  CLIENTES=(C1 C2 C3 C4 C5)
  IP_CLIENTE=([C1]=10.0.19.20 [C2]=10.0.17.20 [C3]=10.0.15.20 [C4]=10.0.24.20 [C5]=10.0.26.20)
  # Bootstrapper no PC do N4 (10.0.21.20). Como o N4 também corre um nó na porta 5000,
  # o bootstrapper usa a porta 5001 (cópia do JSON em /tmp; o configs/cenario3.json não muda).
  BOOT="10.0.21.20 --porta 5001"
  OTT_CLIENTE="python3 -m ott $BOOT --player --ver 0"
  arrancar_base() {
    sed 's/"port": 5000/"port": 5001/' "$PROJ/configs/cenario3.json" > /tmp/cenario3-bootstrapper.json
    no N4 "python3 -m bootstrapper /tmp/cenario3-bootstrapper.json" bootstrapper
    for n in N4 N1 N2 N3 N5 N6; do
      no "$n" "python3 -m ott $BOOT"
    done
  }
  arrancar_servidores() {
    # Os dois servidores têm os mesmos 2 vídeos: o vídeo 0 na porta 24000 e o vídeo 1 na 24001.
    for s in S1 S2; do
      no "$s" "python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000" video0
      no "$s" "python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001" video1
    done
    no S1 "python3 -m ott $BOOT --servidor --servidor"
    no S2 "python3 -m ott $BOOT --servidor --servidor"
  }
  servidor_medicao() {
    no S1 "python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000" video0
    no S1 "python3 -m ott $BOOT --servidor"
  }
  ;;
*)
  uso
  ;;
esac

if [ "$MODO" != normal ]; then
  K="${K:-${#CLIENTES[@]}}"
  [[ "$K" =~ ^[1-9][0-9]*$ ]] && [ "$K" -le "${#CLIENTES[@]}" ] \
    || { echo "--clientes: de 1 a ${#CLIENTES[@]} no cenário $CENARIO" >&2; uso; }
  MEDIDOS=("${CLIENTES[@]:0:$K}")
  REL="logs/medicoes/cenario$CENARIO-$MODO-${K}cli"   # relativo ao projeto, para os comandos
  PASTA="$PROJ/$REL"
fi

# --- Verificações do ambiente e limpeza ---------------------------------------------------------
exigir_root
[ -n "${DISPLAY:-}" ] || { echo "Sem DISPLAY: corre com sudo -E, a partir do ambiente gráfico." >&2; exit 1; }
[ "$JANELAS" = 0 ] || command -v xterm >/dev/null || { echo "Não encontrei o xterm." >&2; exit 1; }
sessao_core
mkdir -p "$LOGS"

n="$(parar_tudo)"
[ "$n" -eq 0 ] || echo "Parei $n programa(s) que tinham ficado de uma execução anterior."
rm -f "$LOGS"/cenario"$CENARIO"-*.log
echo "$CENARIO" > "$LOGS/.cenario_atual"
: > "$LOGS/cenario$CENARIO.comandos"  # para o no.sh conseguir voltar a arrancar um nó

if [ "$MODO" != normal ]; then
  if [ -e "$PASTA" ]; then
    antiga="$PASTA.$(date +%Y%m%d-%H%M%S)"
    mv "$PASTA" "$antiga"
    echo "Já havia uma medição com este nome: passou para $antiga"
  fi
  mkdir -p "$PASTA"
fi

# no NÓ "COMANDO" [SUFIXO]
no() {
  printf '%s\t%s\t%s\n' "$1" "${3:-}" "$2" >> "$LOGS/cenario$CENARIO.comandos"
  lancar "$1" "$2" "${3:-}"
  echo "  $1 ($(papel "$1" "${3:-}")): $2"
  sleep "$ESPERA"
}

# esperar_rede NÓ IP: espera até o NÓ chegar ao IP (encaminhamento dos routers pronto).
esperar_rede() {
  local nome="$1" ip="$2"
  echo "À espera de que o $nome chegue a $ip (encaminhamento dos routers)..."
  for _ in $(seq 1 60); do
    if dentro "$nome" ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then
      echo "  rede pronta"
      return
    fi
    sleep 1
  done
  echo "Ao fim de 60 s o $nome ainda não chega a $ip: o OSPF dos routers está a correr?" >&2
  exit 1
}

abrir_painel() {
  if command -v xterm >/dev/null; then
    xterm -fa Monospace -fs 10 -geometry 150x50 -T "Painel do cenário $CENARIO" \
      -e python3 "$PROJ/scripts/painel.py" "$CENARIO" &
    echo "Abri o painel. Se o fechares: python3 scripts/painel.py $CENARIO"
  else
    echo "Para ver o painel: python3 scripts/painel.py $CENARIO"
  fi
}

# --- Arranque -----------------------------------------------------------------------------------
esperar_rede "$REDE_NO" "$REDE_IP"

case "$MODO" in
normal)
  arrancar_base
  for c in "${CLIENTES[@]}"; do
    no "$c" "python3 player/Cliente.py" player
    no "$c" "$OTT_CLIENTE"
  done
  arrancar_servidores
  echo
  echo "Tudo arrancado. Os logs ficam em logs/cenario$CENARIO-*.log"
  abrir_painel
  exit 0
  ;;
overlay)
  arrancar_base
  servidor_medicao
  for c in "${MEDIDOS[@]}"; do
    no "$c" "python3 player/Medidor.py $REL/$c.csv" medidor
    no "$c" "$OTT_CLIENTE"
  done
  abrir_painel
  ;;
direto)
  for c in "${MEDIDOS[@]}"; do
    no "$c" "python3 player/Medidor.py $REL/$c.csv --ip 0.0.0.0" medidor
    no S1 "python3 player/Servidor.py videos/movie.Mjpeg ${IP_CLIENTE[$c]} 25000" "video-$c"
  done
  ;;
esac

# --- Medição ------------------------------------------------------------------------------------
echo "À espera de $ESPERA_VIDEO s para o vídeo chegar a todos os clientes..."
sleep "$ESPERA_VIDEO"
no S1 "python3 scripts/debito_interface.py eth0 --duracao $DURACAO" debito
echo "A medir durante $DURACAO s (${MEDIDOS[*]}, $MODO)..."
sleep "$((DURACAO - ESPERA))"

# Para os medidores com Ctrl+C (SIGINT): fecham o CSV e escrevem a mensagem final. O padrão só
# apanha o processo python, e não o bash nem o xterm à volta dele.
pkill -INT -f '^python3 player/Medidor\.py' || true
sleep 2

# Cópia dos logs desta execução para a pasta da medição, sem o prefixo cenarioN- (ex: C1.log,
# S1-debito.log). Os originais ficam em logs/, para o painel e o no.sh.
for f in "$LOGS"/cenario"$CENARIO"-*.log; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  cp "$f" "$PASTA/${base#cenario"$CENARIO"-}"
done

echo
echo "=== Análise ($REL) ==="
python3 "$PROJ/scripts/analisar_medicoes.py" "$PASTA"/C*.csv | tee "$PASTA/analise.txt"
echo
echo "Débito à saída do S1: última linha de $REL/S1-debito.log"
tail -n 1 "$PASTA/S1-debito.log" || true
echo
echo "Feito. Antes do próximo teste: sudo bash scripts/no.sh parar-tudo, e Stop e Start no CORE."
