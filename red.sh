#!/usr/bin/env bash
# Topología del taller dentro de un solo Linux (WSL2) con network namespaces:
#
#   [A] emisor  192.168.50.10  vethA ==== vethB  192.168.50.20  receptor [B]
#       cliente iperf3                              servidor iperf3
#       cola TBF 10 Mbit/s (cuello de botella)      captura tcpdump (traza)
#
# Ambos namespaces comparten el reloj del mismo kernel, así que el retardo de cada
# paquete (llegada a B - envío en A) se mide sin error de sincronización.
#
# Uso (como root):  bash red.sh up | status | down
set -euo pipefail

TBF_ARGS="rate 10mbit burst 16kb limit 32kb"

up() {
  ip netns add A
  ip netns add B
  ip link add vethA type veth peer name vethB
  ip link set vethA netns A
  ip link set vethB netns B
  ip -n A addr add 192.168.50.10/24 dev vethA
  ip -n B addr add 192.168.50.20/24 dev vethB
  for ns in A B; do ip -n "$ns" link set lo up; done
  ip -n A link set vethA up
  ip -n B link set vethB up
  ip netns exec A ping -c 2 -W 1 192.168.50.20 >/dev/null && echo "Conectividad A -> B: OK"
  ip netns exec A tc qdisc replace dev vethA root handle 10: tbf $TBF_ARGS
  echo "Cola en A: $(ip netns exec A tc qdisc show dev vethA)"
  ip netns exec B iperf3 -s -D --logfile /tmp/iperf3_servidor.log
  sleep 1
  echo "Servidor iperf3 en B: $(ip netns pids B | wc -l) proceso(s)"
}

status() {
  ip netns list
  ip -n A -br addr; ip -n B -br addr
  ip netns exec A tc -s qdisc show dev vethA
}

down() {
  ip netns pids B 2>/dev/null | xargs -r kill 2>/dev/null || true
  ip netns pids A 2>/dev/null | xargs -r kill 2>/dev/null || true
  ip netns del A 2>/dev/null || true
  ip netns del B 2>/dev/null || true
  echo "Topología eliminada."
}

[[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo)." >&2; exit 1; }
case "${1:-}" in
  up) up ;; down) down ;; status) status ;;
  *) echo "Uso: bash $0 up|status|down" >&2; exit 1 ;;
esac
