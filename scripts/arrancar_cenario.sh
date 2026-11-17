#!/usr/bin/env bash
# Arranca um cenário inteiro no CORE com um só comando: bootstrapper, nós, players e servidores.
#
# Uso (na máquina, fora do CORE, depois de abrir a topologia e carregar em Start):
#   xhost +SI:localuser:root                        # uma vez por sessão gráfica
#   sudo -E bash scripts/arrancar_cenario.sh 3      # cenário 3 (topologias/underlay.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 2      # cenário 2 (topologias/testte2.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 3 --janelas   # uma janela por programa, como antes
#
# Os programas correm sem janela e o que escrevem fica em logs/cenarioN-<nó>[-<programa>].log,
# com o nome do nó em cada linha. Abrem-se só as janelas dos players (com o nome do nó no título)
# e um painel com o estado de todos os nós (scripts/painel.py).
# Para mexer num nó (parar, matar, piorar ou cortar uma ligação): scripts/no.sh.
# Para parar tudo: sudo bash scripts/no.sh parar-tudo, e depois o Stop do CORE.
#
# Ordem: bootstrapper, nós intermédios, clientes e players, e os servidores por último, para o
# 1.º anúncio já encontrar as ligações feitas. No cenário 3 há dois servidores com os mesmos
# 2 vídeos e os clientes pedem o vídeo 0 (--ver 0): cada um fica com o servidor mais barato.
set -euo pipefail

CENARIO="${1:-}"
JANELAS=0
[ "${2:-}" = "--janelas" ] && JANELAS=1
# shellcheck source=comum.sh
source "$(dirname "$0")/comum.sh"
ESPERA=2  # segundos entre programas, para os vizinhos se apresentarem

case "$CENARIO" in
  2|3) ;;
  *) echo "Uso: sudo -E bash $0 2|3 [--janelas]" >&2; exit 1 ;;
esac
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

case "$CENARIO" in
2)
  # Bootstrapper no router n7; cada nó usa o endereço do n7 virado para o seu lado.
  esperar_rede S1 10.0.4.20
  no n7 "python3 -m bootstrapper configs/cenario2.json" bootstrapper
  no N1 "python3 -m ott 10.0.1.1"
  no N2 "python3 -m ott 10.0.2.1"
  no N3 "python3 -m ott 10.0.6.1"
  for c in C1 C2; do
    no "$c" "python3 player/Cliente.py" player
    no "$c" "python3 -m ott 10.0.6.1 --player --ver 1:0"
  done
  no S1 "python3 player/Servidor.py videos/movie.Mjpeg" video
  no S1 "python3 -m ott 10.0.0.1 --servidor"
  ;;
3)
  # Bootstrapper no PC do N4 (10.0.21.20). Como o N4 também corre um nó na porta 5000,
  # o bootstrapper usa a porta 5001 (cópia do JSON em /tmp; o configs/cenario3.json não muda).
  esperar_rede S1 10.0.26.20
  sed 's/"port": 5000/"port": 5001/' "$PROJ/configs/cenario3.json" > /tmp/cenario3-bootstrapper.json
  BOOT="10.0.21.20 --porta 5001"
  no N4 "python3 -m bootstrapper /tmp/cenario3-bootstrapper.json" bootstrapper
  for n in N4 N1 N2 N3 N5 N6; do
    no "$n" "python3 -m ott $BOOT"
  done
  for c in C1 C2 C3 C4 C5; do
    no "$c" "python3 player/Cliente.py" player
    no "$c" "python3 -m ott $BOOT --player --ver 0"
  done
  # Os dois servidores têm os mesmos 2 vídeos: o vídeo 0 na porta 24000 e o vídeo 1 na 24001.
  for s in S1 S2; do
    no "$s" "python3 player/Servidor.py videos/movie.Mjpeg 127.0.0.1 24000" video0
    no "$s" "python3 player/Servidor.py videos/movie2.Mjpeg 127.0.0.1 24001" video1
  done
  no S1 "python3 -m ott $BOOT --servidor --servidor"
  no S2 "python3 -m ott $BOOT --servidor --servidor"
  ;;
esac

echo
echo "Tudo arrancado. Os logs ficam em logs/cenario$CENARIO-*.log"
if command -v xterm >/dev/null; then
  xterm -fa Monospace -fs 10 -geometry 150x50 -T "Painel do cenário $CENARIO" \
    -e python3 "$PROJ/scripts/painel.py" "$CENARIO" &
  echo "Abri o painel. Se o fechares: python3 scripts/painel.py $CENARIO"
else
  echo "Para ver o painel: python3 scripts/painel.py $CENARIO"
fi
