#!/usr/bin/env bash
# Arranca um cenário inteiro no CORE com um só comando: bootstrapper, nós, players e servidor.
#
# Uso (na máquina, fora do CORE, depois de abrir a topologia e carregar em Start):
#   xhost +SI:localuser:root                  # uma vez por sessão gráfica, para as janelas abrirem
#   sudo -E bash scripts/arrancar_cenario.sh 2   # cenário 2 (topologias/testte2.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 3   # cenário 3 (topologias/underlay.xml)
#
# Modo de medição (docs/testes.md, secção 4): em vez dos players, os clientes correm o
# player/Medidor.py, o S1 mede o tráfego da eth0, e no fim o script para os medidores e mostra a
# análise. Tudo fica em logs/medicoes/cenarioN-<modo>-<K>cli/ (C1.csv, ..., S1-debito.log,
# analise.txt). Entre dois testes: Stop e Start no CORE, para começar sempre do zero.
#   sudo -E bash scripts/arrancar_cenario.sh 3 --medir overlay --clientes 2   # com overlay
#   sudo -E bash scripts/arrancar_cenario.sh 3 --medir direto --clientes 2    # sem overlay
#   ... --duracao 90     # segundos de medição depois de todos os clientes arrancarem (60)
# Com --clientes K entram os K primeiros clientes (C1, ..., CK); por omissão, todos.
# Sem overlay não há nós OTT: o S1 corre um Servidor.py por cliente, a enviar direto para ele.
# Com overlay, no cenário 3 só arranca o S1 (com o vídeo 0), para comparar com o envio direto.
#
# Abre uma janela (xterm) por programa, com o nome do nó no título, como o duplo clique no CORE.
# Cada janela também grava o que mostra em logs/cenarioN-<nó>[-<programa>].log.
# Para parar tudo: o botão Stop do CORE.
#
# Ordem: bootstrapper, nós intermédios, clientes e players, e os servidores por último, para o
# 1.º anúncio já encontrar as ligações feitas. No cenário 3 há dois servidores com os mesmos
# 2 vídeos e os clientes pedem o vídeo 0 (--ver 0): cada um fica com o servidor mais barato.
# Na medição com overlay os servidores arrancam antes dos clientes, para o "1.º frame" da análise
# ser o tempo que um cliente novo demora a receber vídeo, e não a espera pelo servidor.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
ESPERA=2         # segundos entre janelas, para os vizinhos se apresentarem
ESPERA_VIDEO=5   # medição: segundos entre o último cliente e o início da medida no S1

uso() {
  echo "Uso: sudo -E bash $0 2|3 [--medir overlay|direto] [--clientes K] [--duracao S]" >&2
  exit 1
}

CENARIO="${1:-}"
[ $# -gt 0 ] && shift
MODO=normal
K=""
DURACAO=60
while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || uso
  case "$1" in
    --medir) MODO="$2" ;;
    --clientes) K="$2" ;;
    --duracao) DURACAO="$2" ;;
    *) uso ;;
  esac
  shift 2
done
case "$MODO" in normal | overlay | direto) ;; *) echo "--medir: overlay ou direto" >&2; uso ;; esac
[[ "$DURACAO" =~ ^[1-9][0-9]*$ ]] || { echo "--duracao: um número inteiro de segundos" >&2; uso; }
[ "$MODO" != normal ] || [ -z "$K" ] || { echo "--clientes só faz sentido com --medir" >&2; uso; }

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

# --- Verificações do ambiente -------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || { echo "Corre com sudo -E (os nós do CORE só aceitam comandos do root)." >&2; exit 1; }
command -v vcmd >/dev/null || { echo "Não encontrei o vcmd (vem com o CORE)." >&2; exit 1; }
command -v xterm >/dev/null || { echo "Não encontrei o xterm." >&2; exit 1; }
[ -n "${DISPLAY:-}" ] || { echo "Sem DISPLAY: corre com sudo -E, a partir do ambiente gráfico." >&2; exit 1; }

sessoes=(/tmp/pycore.*)
[ -d "${sessoes[0]}" ] || { echo "Não há nenhuma sessão do CORE a correr: abre a topologia e carrega em Start." >&2; exit 1; }
[ "${#sessoes[@]}" -eq 1 ] || { echo "Há mais do que uma sessão do CORE (${sessoes[*]}): fecha as outras." >&2; exit 1; }
SESSAO="${sessoes[0]}"
mkdir -p "$PROJ/logs"
LOG_PREFIXO="$PROJ/logs/cenario$CENARIO-"

if [ "$MODO" != normal ]; then
  if [ -e "$PASTA" ]; then
    antiga="$PASTA.$(date +%Y%m%d-%H%M%S)"
    mv "$PASTA" "$antiga"
    echo "Já havia uma medição com este nome: passou para $antiga"
  fi
  mkdir -p "$PASTA"
  LOG_PREFIXO="$PASTA/"
fi

# no NOME "COMANDO" [SUFIXO]: abre uma janela a correr COMANDO dentro do nó NOME.
no() {
  local nome="$1" cmd="$2" sufixo="${3:-}"
  local titulo="$nome${sufixo:+ ($sufixo)}"
  local log="$LOG_PREFIXO$nome${sufixo:+-$sufixo}.log"
  [ -e "$SESSAO/$nome" ] || { echo "O nó $nome não existe nesta sessão: é a topologia certa?" >&2; exit 1; }
  xterm -T "$titulo" -geometry 110x24 -e vcmd -c "$SESSAO/$nome" -- bash -c \
    "cd '$PROJ' && export DISPLAY='$DISPLAY' PYTHONUNBUFFERED=1 && $cmd 2>&1 | tee '$log'; echo; echo '[terminou: Enter para fechar]'; read" &
  echo "  $titulo: $cmd"
  sleep "$ESPERA"
}

# esperar_rede NO IP: espera até NO conseguir chegar a IP (encaminhamento dos routers pronto).
esperar_rede() {
  local nome="$1" ip="$2"
  echo "À espera de que o $nome chegue a $ip (encaminhamento dos routers)..."
  for _ in $(seq 1 60); do
    if vcmd -c "$SESSAO/$nome" -- ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then
      echo "  rede pronta"
      return
    fi
    sleep 1
  done
  echo "Ao fim de 60 s o $nome ainda não chega a $ip: o OSPF dos routers está a correr?" >&2
  exit 1
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
  echo "Tudo arrancado. Os logs ficam em $PROJ/logs/cenario$CENARIO-*.log"
  exit 0
  ;;
overlay)
  arrancar_base
  servidor_medicao
  for c in "${MEDIDOS[@]}"; do
    no "$c" "python3 player/Medidor.py $REL/$c.csv" medidor
    no "$c" "$OTT_CLIENTE"
  done
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

# Para os medidores com Ctrl+C (SIGINT): fecham o CSV e mostram a mensagem final. O padrão só
# apanha o processo python, e não o bash nem o xterm à volta dele.
pkill -INT -f '^python3 player/Medidor\.py' || true
sleep 2

echo
echo "=== Análise ($REL) ==="
python3 "$PROJ/scripts/analisar_medicoes.py" "$PASTA"/C*.csv | tee "$PASTA/analise.txt"
echo
echo "Débito à saída do S1: última linha de $REL/S1-debito.log"
tail -n 1 "$PASTA/S1-debito.log" || true
echo
echo "Feito. Antes do próximo teste: Stop e Start no CORE."
