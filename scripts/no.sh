#!/usr/bin/env bash
# Mexe num nó do cenário que está a correr, a partir do teu terminal normal (onde colar funciona).
# Usa a sessão do CORE aberta e o cenário arrancado pelo scripts/arrancar_cenario.sh.
#
#   sudo bash scripts/no.sh parar N1               # kill (SIGTERM): sai e avisa os vizinhos (CLOSE)
#   sudo bash scripts/no.sh matar N1               # kill -9: o nó morre sem avisar (falha a sério)
#   sudo bash scripts/no.sh arrancar N1            # volta a arrancar o nó, com o comando do arranque
#   sudo bash scripts/no.sh piorar C1 N1 delay 50ms   # mais atraso na ligação C1–N1 (nos dois sentidos)
#   sudo bash scripts/no.sh piorar C1 N1 loss 2%      # perda na ligação C1–N1 (nos dois sentidos)
#   sudo bash scripts/no.sh cortar C1 N1           # 100% de perda na ligação C1–N1
#   sudo bash scripts/no.sh repor C1 N1            # tira o que o piorar/cortar pôs nessa ligação
#   sudo bash scripts/no.sh correr C1 tc -s qdisc show   # corre um comando qualquer dentro do nó
#   sudo bash scripts/no.sh parar-tudo             # para todos os programas (antes do Stop do CORE)
#
# parar, matar e arrancar mexem no nó overlay. Para o player, o servidor de vídeo ou o
# bootstrapper, junta no fim: player, video0, video1 ou bootstrapper (ex: parar S1 video0).
# (video sozinho apanha todos os servidores de vídeo desse nó.)
# Cada ação fica escrita no log dos nós envolvidos, para aparecer no painel e nos prints.
#
# piorar/cortar põem um netem no tráfego para o IP do outro nó (como no docs/testes.md). Cada
# nó só tem uma regra destas de cada vez: piorar outra ligação no mesmo nó substitui a anterior.
set -euo pipefail

# shellcheck source=comum.sh
source "$(dirname "$0")/comum.sh"

uso() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

ACAO="${1:-}"
[ -n "$ACAO" ] || uso
shift
exigir_root
sessao_core
[ -f "$LOGS/.cenario_atual" ] || { echo "Não sei que cenário está a correr: arranca-o com scripts/arrancar_cenario.sh." >&2; exit 1; }
CENARIO="$(cat "$LOGS/.cenario_atual")"

padrao_de() {  # padrao_de [SUFIXO]: o comando do programa a que a ação se aplica
  case "${1:-}" in
    "") echo "$PADRAO_OTT" ;;
    player) echo '^python3 player/Cliente\.py' ;;
    video) echo '^python3 player/Servidor\.py' ;;
    video[0-9]) echo "^python3 player/Servidor\\.py .* $((24000 + ${1#video}))\$" ;;
    bootstrapper) echo '^python3 -m bootstrapper' ;;
    *) echo "Programa desconhecido: $1 (usa player, video0, video1 ou bootstrapper)." >&2; exit 1 ;;
  esac
}

# sinal NÓ SINAL TEXTO [SUFIXO]: manda o SINAL ao programa desse nó.
sinal() {
  local no="$1" sig="$2" texto="$3" sufixo="${4:-}" pids
  exigir_no "$no"
  pids="$(pids_no "$no" "$(padrao_de "$sufixo")")"
  [ -n "$pids" ] || { echo "No $no não está a correr nenhum $(papel "$no" "$sufixo")." >&2; exit 1; }
  marcar "$no" "$texto" "$sufixo"
  # shellcheck disable=SC2086
  kill "-$sig" $pids
  echo "$(hora)  $no: $texto"
}

# tc_para A B [NETEM...]: aplica o NETEM ao tráfego que sai de A para o IP de B
# (sem NETEM, só tira o que lá estava).
tc_para() {
  local a="$1" b="$2" ipb dev
  shift 2
  ipb="$(ip_do_no "$b")"
  [ -n "$ipb" ] || { echo "Não encontrei o IP do $b." >&2; exit 1; }
  dev="$(dentro "$a" ip route get "$ipb" | grep -o 'dev [^ ]*' | head -1 | cut -d' ' -f2)"
  dentro "$a" tc qdisc del dev "$dev" root 2>/dev/null || true
  [ "$#" -gt 0 ] || return 0
  dentro "$a" tc qdisc add dev "$dev" root handle 1: prio
  dentro "$a" tc qdisc add dev "$dev" parent 1:3 handle 30: netem "$@"
  dentro "$a" tc filter add dev "$dev" parent 1:0 protocol ip u32 match ip dst "$ipb/32" flowid 1:3
}

# ligacao A B TEXTO [NETEM...]: aplica nos dois sentidos e regista nos dois logs.
ligacao() {
  local a="$1" b="$2" texto="$3"
  shift 3
  exigir_no "$a"
  exigir_no "$b"
  tc_para "$a" "$b" "$@"
  tc_para "$b" "$a" "$@"
  marcar "$a" "ligação $a–$b: $texto"
  marcar "$b" "ligação $a–$b: $texto"
  echo "$(hora)  ligação $a–$b: $texto"
}

case "$ACAO" in
  parar)
    [ "$#" -ge 1 ] || uso
    sinal "$1" TERM "parado com kill: sai a avisar os vizinhos (CLOSE)" "${2:-}"
    ;;
  matar)
    [ "$#" -ge 1 ] || uso
    sinal "$1" KILL "morto com kill -9 (sem aviso)" "${2:-}"
    ;;
  arrancar)
    [ "$#" -ge 1 ] || uso
    no="$1" sufixo="${2:-}"
    exigir_no "$no"
    [ -z "$(pids_no "$no" "$(padrao_de "$sufixo")")" ] || { echo "O $no já está a correr." >&2; exit 1; }
    cmd="$(awk -F'\t' -v n="$no" -v s="$sufixo" '$1 == n && $2 == s { print $3; exit }' \
      "$LOGS/cenario$CENARIO.comandos")"
    [ -n "$cmd" ] || { echo "Não sei como arrancar o $no${sufixo:+ ($sufixo)} no cenário $CENARIO." >&2; exit 1; }
    marcar "$no" "arrancado de novo" "$sufixo"
    lancar "$no" "$cmd" "$sufixo"
    echo "$(hora)  $no: arrancado de novo ($cmd)"
    ;;
  piorar)
    [ "$#" -ge 3 ] || uso
    a="$1" b="$2"
    shift 2
    ligacao "$a" "$b" "$*" "$@"
    ;;
  cortar)
    [ "$#" -eq 2 ] || uso
    ligacao "$1" "$2" "cortada (loss 100%)" loss 100%
    ;;
  repor)
    [ "$#" -eq 2 ] || uso
    ligacao "$1" "$2" "reposta"
    ;;
  correr)
    [ "$#" -ge 2 ] || uso
    no="$1"
    shift
    exigir_no "$no"
    dentro "$no" "$@"
    ;;
  parar-tudo)
    echo "Parei $(parar_tudo) programa(s). Já podes carregar no Stop do CORE."
    ;;
  *)
    uso
    ;;
esac
