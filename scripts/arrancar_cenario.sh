#!/usr/bin/env bash
# Arranca um cenário inteiro no CORE com um só comando: bootstrapper, nós, players e servidor.
#
# Uso (na máquina, fora do CORE, depois de abrir a topologia e carregar em Start):
#   xhost +SI:localuser:root                  # uma vez por sessão gráfica, para as janelas abrirem
#   sudo -E bash scripts/arrancar_cenario.sh 2   # cenário 2 (topologias/testte2.xml)
#   sudo -E bash scripts/arrancar_cenario.sh 3   # cenário 3 (topologias/underlay.xml)
#
# Abre uma janela (xterm) por programa, com o nome do nó no título, como o duplo clique no CORE.
# Cada janela também grava o que mostra em logs/cenarioN-<nó>[-<programa>].log.
# Para parar tudo: o botão Stop do CORE.
#
# Ordem: bootstrapper, nós intermédios, clientes e players, e o nó do servidor por último
# (nesta versão das rotas, o primeiro pai que um nó escolhe fica para sempre).
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
CENARIO="${1:-}"
ESPERA=2  # segundos entre nós, para os vizinhos se apresentarem

[ "$(id -u)" -eq 0 ] || { echo "Corre com sudo -E (os nós do CORE só aceitam comandos do root)." >&2; exit 1; }
command -v vcmd >/dev/null || { echo "Não encontrei o vcmd (vem com o CORE)." >&2; exit 1; }
command -v xterm >/dev/null || { echo "Não encontrei o xterm." >&2; exit 1; }
[ -n "${DISPLAY:-}" ] || { echo "Sem DISPLAY: corre com sudo -E, a partir do ambiente gráfico." >&2; exit 1; }

sessoes=(/tmp/pycore.*)
[ -d "${sessoes[0]}" ] || { echo "Não há nenhuma sessão do CORE a correr: abre a topologia e carrega em Start." >&2; exit 1; }
[ "${#sessoes[@]}" -eq 1 ] || { echo "Há mais do que uma sessão do CORE (${sessoes[*]}): fecha as outras." >&2; exit 1; }
SESSAO="${sessoes[0]}"
mkdir -p "$PROJ/logs"

# no NOME "COMANDO" [SUFIXO]: abre uma janela a correr COMANDO dentro do nó NOME.
no() {
  local nome="$1" cmd="$2" sufixo="${3:-}"
  local titulo="$nome${sufixo:+ ($sufixo)}"
  local log="$PROJ/logs/cenario$CENARIO-$nome${sufixo:+-$sufixo}.log"
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
  for n in N4 N1 N2 N3 N5 N6 S2; do
    no "$n" "python3 -m ott $BOOT"
  done
  for c in C1 C2 C3 C4 C5; do
    no "$c" "python3 player/Cliente.py" player
    no "$c" "python3 -m ott $BOOT --player --ver 17:0"
  done
  no S1 "python3 player/Servidor.py videos/movie.Mjpeg" video
  no S1 "python3 -m ott $BOOT --servidor"
  ;;
*)
  echo "Uso: sudo -E bash $0 2|3" >&2
  exit 1
  ;;
esac

echo
echo "Tudo arrancado. Os logs ficam em $PROJ/logs/cenario$CENARIO-*.log"
