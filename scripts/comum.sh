# Funções partilhadas pelos scripts que mexem nos nós do CORE (arrancar_cenario.sh e no.sh).
# Não é para correr sozinho: os outros scripts fazem "source" dele.

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$PROJ/logs"

# Comandos dos nossos programas, tal como aparecem na lista de processos.
PADRAO_OTT='^python3 -m ott( |$)'
PADRAO_TODOS='^python3 (-m (ott|bootstrapper)|player/(Servidor|Cliente|Medidor)\.py|scripts/debito_interface\.py)( |$)'

exigir_root() {
  [ "$(id -u)" -eq 0 ] || { echo "Corre com sudo (os nós do CORE só aceitam comandos do root)." >&2; exit 1; }
  command -v vcmd >/dev/null || { echo "Não encontrei o vcmd (vem com o CORE)." >&2; exit 1; }
}

# sessao_core: põe em SESSAO a pasta da sessão do CORE que está a correr.
sessao_core() {
  local sessoes=(/tmp/pycore.*)
  [ -d "${sessoes[0]}" ] || { echo "Não há nenhuma sessão do CORE a correr: abre a topologia e carrega em Start." >&2; exit 1; }
  [ "${#sessoes[@]}" -eq 1 ] || { echo "Há mais do que uma sessão do CORE (${sessoes[*]}): fecha as outras." >&2; exit 1; }
  SESSAO="${sessoes[0]}"
}

exigir_no() {
  [ -e "$SESSAO/$1" ] || { echo "O nó $1 não existe nesta sessão do CORE (é a topologia certa?)." >&2; exit 1; }
}

# dentro NÓ COMANDO...: corre um comando dentro do nó e espera que acabe.
dentro() { local no="$1"; shift; vcmd -c "$SESSAO/$no" -- "$@"; }

hora() { date +%H:%M:%S.%3N; }

# ip_do_no NÓ: o primeiro endereço IPv4 do nó (sem ser o loopback).
ip_do_no() {
  dentro "$1" ip -4 -o addr show 2>/dev/null | awk '$2 != "lo" { split($4, a, "/"); print a[1]; exit }'
}

# id_do_no NÓ: o número do nó no JSON do cenário (vazio se não for um nó do overlay).
id_do_no() {
  local ip; ip="$(ip_do_no "$1")"
  [ -n "$ip" ] || return 0
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["nodes"].get(sys.argv[2], ""))' \
    "$PROJ/configs/cenario$CENARIO.json" "$ip" 2>/dev/null || true
}

# papel NÓ [SUFIXO]: o que corre nessa janela/log, em palavras.
papel() {
  case "${2:-}" in
    bootstrapper) echo "bootstrapper"; return ;;
    player) echo "player"; return ;;
    medidor) echo "medidor"; return ;;
    debito) echo "débito da eth0"; return ;;
    video*) echo "servidor de vídeo${2#video}"; return ;;
  esac
  case "$1" in
    S*) echo "servidor" ;;
    C*) echo "cliente" ;;
    N*) echo "nó" ;;
    *) echo "router" ;;
  esac
}

log_de() { echo "$LOGS/cenario$CENARIO-$1${2:+-$2}.log"; }

# marcar NÓ TEXTO [SUFIXO]: deixa uma linha no log do nó, para o painel e para os prints.
marcar() { echo "$(hora) $1 INFO no.sh: $2" >> "$(log_de "$1" "${3:-}")"; }

# lancar NÓ "COMANDO" [SUFIXO]: corre o COMANDO dentro do nó, em segundo plano, com o que ele
# escreve no log do nó. Com JANELAS=1 abre antes uma janela (xterm), como o duplo clique no CORE.
lancar() {
  local no="$1" cmd="$2" sufixo="${3:-}"
  local log id titulo env
  exigir_no "$no"
  log="$(log_de "$no" "$sufixo")"
  id="$(id_do_no "$no")"
  titulo="$no${id:+ [$id]} · $(papel "$no" "$sufixo")"
  env="cd '$PROJ' && export DISPLAY='${DISPLAY:-}' PYTHONUNBUFFERED=1 OTT_NOME='$no' OTT_ID='$id'"
  if [ "${JANELAS:-0}" = 1 ]; then
    xterm -fa Monospace -fs 10 -T "$titulo" -geometry 110x24 -e vcmd -c "$SESSAO/$no" -- bash -c \
      "$env && $cmd 2>&1 | tee -a '$log'; echo; echo '[terminou: Enter para fechar]'; read" &
  else
    setsid vcmd -c "$SESSAO/$no" -- bash -c "$env && exec $cmd" >> "$log" 2>&1 < /dev/null &
  fi
}

# pids_no NÓ PADRÃO: PIDs dos processos cujo comando bate com PADRÃO e que correm dentro do NÓ.
# (Os nós do CORE só separam a rede, por isso compara-se a rede de cada processo com a do nó.)
pids_no() {
  local ns pid
  ns="$(dentro "$1" readlink /proc/self/ns/net)"
  for pid in $(pgrep -f "$2" || true); do
    [ "$(readlink "/proc/$pid/ns/net" 2>/dev/null)" = "$ns" ] && echo "$pid"
  done
  return 0
}

# parar_tudo: para todos os nossos programas que correm dentro de nós do CORE e escreve
# quantos eram. Os nós e o bootstrapper recebem Ctrl+C (os nós avisam os vizinhos com CLOSE);
# o que ainda estiver vivo 2 s depois é morto.
parar_tudo() {
  local host pid n=0 cmd
  host="$(readlink /proc/1/ns/net)"
  for pid in $(pgrep -f "$PADRAO_TODOS" || true); do
    [ "$(readlink "/proc/$pid/ns/net" 2>/dev/null)" != "$host" ] || continue
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$cmd" in
      *"-m ott"*|*"-m bootstrapper"*) kill -INT "$pid" 2>/dev/null || true ;;
      *) kill -TERM "$pid" 2>/dev/null || true ;;
    esac
    n=$((n + 1))
  done
  if [ "$n" -gt 0 ]; then
    sleep 2
    for pid in $(pgrep -f "$PADRAO_TODOS" || true); do
      [ "$(readlink "/proc/$pid/ns/net" 2>/dev/null)" != "$host" ] && kill -KILL "$pid" 2>/dev/null || true
    done
  fi
  echo "$n"
}
