#!/usr/bin/env bash

set -Eeuo pipefail

NUM_UES="${1:?Usage: start_gnu_fixed_channel.sh NUM_UES [ATTENUATION_DB]}"
ATTENUATION_DB="${2:-6}"
UE_CONFIG="${UE_CONFIG:-/srsran/config/ue0.conf}"

SAMPLE_RATE="${SAMPLE_RATE:-$(
  python3 /srsran/config/fixed_channel.py sample-rate "$UE_CONFIG"
)}"

exec python3 -u /srsran/config/multi_ue_fixed_channel.py \
  --num-ues "$NUM_UES" \
  --attenuation-db "$ATTENUATION_DB" \
  --sample-rate "$SAMPLE_RATE"
