#!/usr/bin/env bash

set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-open5gs}"
UE_NUMBER="${UE_NUMBER:-1}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"

GNURADIO_LOG="/tmp/baseline-gnuradio.log"
GNB_LOG="/tmp/baseline-gnb.log"
UE_LOG="/tmp/baseline-ue${UE_NUMBER}.log"

usage() {
  cat <<EOF
Usage: $0 {start|status|logs|stop}

Environment variables:
  NAMESPACE     Kubernetes namespace (default: open5gs)
  UE_NUMBER     UE number to start (default: 1)
  WAIT_SECONDS  Maximum attachment wait time (default: 90)
EOF
}

get_pod() {
  local selector="$1"
  kubectl get pods -n "$NAMESPACE" -l "$selector" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}'
}

resolve_pods() {
  GNB_POD="$(get_pod 'app=srsran,component=gnb')"
  UE_POD="$(get_pod 'app=srsran,component=ue,name=ue1')"

  if [[ -z "$GNB_POD" || -z "$UE_POD" ]]; then
    echo "The running gNB or UE pod could not be found in namespace $NAMESPACE." >&2
    exit 1
  fi
}

exec_gnb() {
  kubectl exec -n "$NAMESPACE" "$GNB_POD" -c gnb -- bash -lc "$1"
}

exec_ue() {
  kubectl exec -n "$NAMESPACE" "$UE_POD" -c ue -- bash -lc "$1"
}

start_component() {
  local name="$1"
  local check_command="$2"
  local start_command="$3"
  local target="$4"

  if "$target" "$check_command" >/dev/null 2>&1; then
    echo "$name is already running."
    return
  fi

  "$target" "$start_command"
  echo "Started $name."
}

start_baseline() {
  resolve_pods

  start_component \
    "GNU Radio" \
    "pgrep -f '[m]ulti_ue_scenario.py' >/dev/null" \
    "cd /; nohup bash -c 'tail -f /dev/null | /srsran/config/start_gnu.sh ${UE_NUMBER}' >${GNURADIO_LOG} 2>&1 &" \
    exec_ue

  sleep 2

  start_component \
    "gNB" \
    "pgrep -f '[/]srsran/gnb' >/dev/null" \
    "nohup /srsran/config/start_gnb.sh >${GNB_LOG} 2>&1 </dev/null &" \
    exec_gnb

  sleep 2

  start_component \
    "UE ${UE_NUMBER}" \
    "pgrep -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' >/dev/null" \
    "nohup /srsran/config/start_ue.sh ${UE_NUMBER} >${UE_LOG} 2>&1 </dev/null &" \
    exec_ue

  echo "Waiting for UE ${UE_NUMBER} to establish a PDU session..."
  for ((second = 1; second <= WAIT_SECONDS; second++)); do
    if exec_ue "grep -q 'PDU Session Establishment successful' '${UE_LOG}'" >/dev/null 2>&1; then
      exec_ue \
        "ip netns exec ue${UE_NUMBER} ip route replace default via 10.41.0.1"
      echo "Baseline ready. UE ${UE_NUMBER} is attached and its default route is set."
      status_baseline
      return
    fi

    if ! exec_ue \
      "pgrep -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' >/dev/null" \
      >/dev/null 2>&1; then
      echo "The UE process stopped before attachment completed." >&2
      exec_ue "tail -n 40 '${UE_LOG}'" || true
      exit 1
    fi

    sleep 1
  done

  echo "Timed out after ${WAIT_SECONDS}s waiting for UE attachment." >&2
  echo "Run '$0 logs' to inspect the component logs." >&2
  exit 1
}

status_baseline() {
  resolve_pods

  printf "%-12s %s\n" "Component" "Status"
  printf "%-12s %s\n" "GNU Radio" \
    "$(exec_ue "pgrep -f '[m]ulti_ue_scenario.py' >/dev/null && echo running || echo stopped")"
  printf "%-12s %s\n" "gNB" \
    "$(exec_gnb "pgrep -f '[/]srsran/gnb' >/dev/null && echo running || echo stopped")"
  printf "%-12s %s\n" "UE ${UE_NUMBER}" \
    "$(exec_ue "pgrep -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' >/dev/null && echo running || echo stopped")"

  echo
  exec_ue \
    "ip netns exec ue${UE_NUMBER} ip -br addr show tun_srsue 2>/dev/null || true"
  exec_ue \
    "ip netns exec ue${UE_NUMBER} ip route 2>/dev/null || true"
}

show_logs() {
  resolve_pods

  echo "===== GNU Radio ====="
  exec_ue "tail -n 25 '${GNURADIO_LOG}' 2>/dev/null || echo 'No GNU Radio log yet.'"
  echo
  echo "===== gNB ====="
  exec_gnb "tail -n 40 '${GNB_LOG}' 2>/dev/null || echo 'No gNB log yet.'"
  echo
  echo "===== UE ${UE_NUMBER} ====="
  exec_ue "tail -n 40 '${UE_LOG}' 2>/dev/null || echo 'No UE log yet.'"
}

stop_baseline() {
  resolve_pods

  exec_ue \
    "pkill -INT -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' 2>/dev/null || true"
  sleep 2
  exec_gnb "pkill -INT -f '[/]srsran/gnb' 2>/dev/null || true"
  sleep 2
  exec_ue "pkill -INT -f '[m]ulti_ue_scenario.py' 2>/dev/null || true"
  exec_ue "pkill -TERM -f '[t]ail -f /dev/null' 2>/dev/null || true"

  echo "Stopped UE ${UE_NUMBER}, gNB, and GNU Radio."
}

case "${1:-}" in
  start)
    start_baseline
    ;;
  status)
    status_baseline
    ;;
  logs)
    show_logs
    ;;
  stop)
    stop_baseline
    ;;
  *)
    usage
    exit 1
    ;;
esac
