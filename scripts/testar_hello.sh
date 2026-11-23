#!/usr/bin/env bash
# Os 4 testes do HELLO único no CORE (cenário 3), seguidos e sem intervenção.
#
# Uso, com o topologias/cenario3.xml aberto no CORE e depois de carregar em Start:
#   xhost +SI:localuser:root
#   sudo -E bash scripts/testar_hello.sh
#
# Arranca o cenário (scripts/arrancar_cenario.sh) e faz, sempre na ligação C1–N1 (o N1 é o pai
# do C1 para o S1):
#   1. arranque: o overlay forma-se e os clientes recebem vídeo;
#   2. cortar a ligação C1–N1 e repô-la: ficam em baixo e voltam a ligar-se sozinhos;
#   3. parar o N1 (Ctrl+C) e voltar a arrancá-lo: saída imediata (CLOSE) e regresso;
#   4. matar o N1 (kill -9) e voltar a arrancá-lo: em baixo pelo silêncio e reinício detetado.
# Antes e depois de cada passo guarda o painel em logs/teste-hello/NN-<passo>.txt. Os logs dos
# nós ficam em logs/cenario3-*.log, como sempre. No fim deixa tudo a correr, para os prints.
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(id -u)" -eq 0 ] || { echo "Corre com sudo -E." >&2; exit 1; }
OUT=logs/teste-hello
mkdir -p "$OUT"
rm -f "$OUT"/*.txt

foto() { python3 scripts/painel.py 3 --uma-vez > "$OUT/$1.txt" 2>&1 || true; }
passo() { echo; echo "== $(date +%T.%3N)  $*" | tee -a "$OUT/00-passos.txt"; }
esperar() { echo "   (espera ${1} s)"; sleep "$1"; }

passo "0. arranque do cenário 3"
bash scripts/arrancar_cenario.sh 3
esperar 25
foto 01-arranque

passo "1. cortar a ligação C1–N1"
bash scripts/no.sh cortar C1 N1
esperar 8
foto 02-ligacao-cortada
passo "1. repor a ligação C1–N1"
bash scripts/no.sh repor C1 N1
esperar 10
foto 03-ligacao-reposta

passo "2. parar o N1 (Ctrl+C, envia CLOSE)"
bash scripts/no.sh parar N1
esperar 6
foto 04-n1-parado
passo "2. arrancar o N1"
bash scripts/no.sh arrancar N1
esperar 10
foto 05-n1-de-volta

passo "3. matar o N1 (kill -9, sem aviso)"
bash scripts/no.sh matar N1
esperar 6
foto 06-n1-morto
passo "3. arrancar o N1"
bash scripts/no.sh arrancar N1
esperar 10
foto 07-n1-reiniciado

passo "fim: tudo continua a correr (para parar: sudo bash scripts/no.sh parar-tudo)"
echo "Resultados em $OUT/ e logs/cenario3-*.log"
