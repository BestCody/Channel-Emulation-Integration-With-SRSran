#!/usr/bin/env bash

set -Eeuo pipefail

NUM_UES="${1:?Usage: start_gnu_multipath.sh NUM_UES [TAPS_FILE]}"
TAPS_FILE="${2:-/srsran/config/fixed_three_path.json}"
UE_CONFIG="${UE_CONFIG:-/srsran/config/ue0.conf}"

SAMPLE_RATE="${SAMPLE_RATE:-$(
  python3 /srsran/config/fixed_channel.py sample-rate "$UE_CONFIG"
)}"

exec python3 -u /srsran/config/multi_ue_fixed_channel.py \
  --num-ues "$NUM_UES" \
  --taps-file "$TAPS_FILE" \
  --sample-rate "$SAMPLE_RATE"
