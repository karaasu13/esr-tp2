#!/usr/bin/env bash
# Corre a demo do transporte no nó do CORE onde é executado (topologias/teste1.xml).
# Uso, no terminal de qualquer nó:
#   bash /home/corvo/Projectos/ESR/tp2/projeto/scripts/demo-cenario1.sh
# O nó é reconhecido pelo nome (S1, O1, C1, C2); IDs e IPs são os da topologia.
# Com um argumento (ex: perda20), grava também o output em logs/<nó>-<argumento>.log
export TERM=${TERM:-xterm}
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
no=$(cat /proc/sys/kernel/hostname)
if [ -n "${1:-}" ]; then
  mkdir -p logs
  exec > >(tee "logs/$no-$1.log") 2>&1
  echo "a gravar em logs/$no-$1.log"
fi
# Se ficou uma demo antiga a ocupar a porta 5000 neste nó, para-a primeiro.
# O ss só vê os sockets deste nó (network namespace), por isso não mexe nos outros.
velho=$(ss -Hulpn 'sport = :5000' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
if [ -n "$velho" ]; then
  echo "a parar a demo antiga (pid $velho)"
  kill $velho 2>/dev/null; sleep 1
  # Um processo suspenso (Ctrl+Z) não sai com o SIGTERM; o SIGKILL apanha-o sempre.
  if ss -Hulpn 'sport = :5000' | grep -q pid=; then kill -9 $velho 2>/dev/null; sleep 0.5; fi
fi
echo "nó $no, a correr a demo (Ctrl+C para parar)"
case "$no" in
  O1) exec python3 -m ott.demo_transporte --id 2 1=10.0.2.21 3=10.0.0.20 4=10.0.1.20 ;;
  S1) exec python3 -m ott.demo_transporte --id 1 2=10.0.2.20 ;;
  C1) exec python3 -m ott.demo_transporte --id 3 2=10.0.0.21 ;;
  C2) exec python3 -m ott.demo_transporte --id 4 2=10.0.1.21 ;;
  *) echo "nó desconhecido: $no (esperava S1, O1, C1 ou C2)"; exit 1 ;;
esac
