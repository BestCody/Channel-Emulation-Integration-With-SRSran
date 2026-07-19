#!/usr/bin/env bash

set -Eeuo pipefail

NUM_UES="${1:?Usage: start_gnu_live_channel.sh NUM_UES}"
UE_CONFIG="${UE_CONFIG:-/srsran/config/ue0.conf}"
CONTROL_BIND="${CONTROL_BIND:-tcp://0.0.0.0:5555}"
GNB_ANTENNAS="${SRSRAN_GNB_ANTENNAS:-1}"
SAMPLE_RATE="${SAMPLE_RATE:-$(
  python3 /srsran/config/fixed_channel.py sample-rate "$UE_CONFIG"
)}"

exec python3 -u /srsran/config/multi_ue_live_channel.py \
  --num-ues "$NUM_UES" \
  --sample-rate "$SAMPLE_RATE" \
  --gnb-antennas "$GNB_ANTENNAS" \
  --control-bind "$CONTROL_BIND"
