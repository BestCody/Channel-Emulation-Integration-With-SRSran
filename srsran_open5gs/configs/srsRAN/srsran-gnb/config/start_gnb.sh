#!/usr/bin/env bash

set -Eeuo pipefail

TEMPLATE="${GNB_CONFIG_TEMPLATE:-/srsran/config/srsran-gnb.yaml}"
RENDERED="${GNB_CONFIG_RENDERED:-/tmp/srsran-gnb.yaml}"

python3 /srsran/config/render_gnb_config.py "$TEMPLATE" "$RENDERED"
exec /srsran/gnb -c "$RENDERED"
