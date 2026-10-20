#!/usr/bin/env bash
# The night load on dev (T-4889), for the deployer's batch between 01:00 and 04:00Z: the Portal grid
# and MCP reads (platform-surfaces.js), 200 readers of the Portal's live view and, when a device
# identity of a scratch space is set, 500 MQTT devices at 1 msg/s (streams.py). Paced so dev stays
# usable: 5 req/s per k6 shape. Never the ETSI TTF suite. Beside the run, `kubectl top pod` every
# 30 s records the CPU and memory of the broker, gateway, Portal and PostgreSQL pods.
#
#   ENDPOINT_URL=https://api.dev.joinedcontext.com/api/endpoint/<slug> ENTITY_TYPE=<type> \
#   MCP_URL=https://api.dev.joinedcontext.com/api/mcp/<project>/<server> \
#   LIVE_URL='https://portal.dev.joinedcontext.com/api/v1/projects/<p>/spaces/<s>/live?type=<type>' \
#   GATEWAY_TOKEN=<viewer token> KUBECONFIG=.../kubeconfig-dev.yaml tests/k6/night.sh
#
# Optional: MQTT_HOST (+ MQTT_PORT 8883, MQTT_TOPIC, MQTT_USER, MQTT_PASSWORD, LANDED_URL),
# JC_K6_DURATION (5m), OUT (reports/night-<UTC stamp>). The identities hold read grants (viewer)
# and a scratch-space device grant (MQTT) only, and the edge's bot limits stay on: a 429 is a
# failure of the run, not of the platform's limiter. A part without its inputs is reported NOT RUN.
set -uo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
hour=$(date -u +%H)
if [ "${JC_NIGHT_ANY_HOUR:-}" != 1 ] && { [ "$hour" -lt 1 ] || [ "$hour" -ge 4 ]; }; then
  echo "night.sh runs between 01:00 and 04:00Z (now ${hour}Z); JC_NIGHT_ANY_HOUR=1 for a local stub" >&2
  exit 2
fi
: "${ENDPOINT_URL:?set ENDPOINT_URL}" "${ENTITY_TYPE:?set ENTITY_TYPE}"
OUT=${OUT:-$here/reports/night-$(date -u +%Y%m%dT%H%MZ)}
mkdir -p "$OUT"
export JC_REPORTS_DIR="$OUT" JC_K6_DURATION="${JC_K6_DURATION:-5m}"

top_loop() {  # kubectl top of the platform's pods every 30 s, until killed
  while :; do
    { date -u +%FT%TZ; kubectl top pod -A --no-headers 2>&1 \
        | grep -E 'antares|broker|gateway|portal|postgres|cnpg' ; } >> "$OUT/top.txt"
    sleep 30
  done
}
TOP=
if [ -n "${KUBECONFIG:-}" ]; then top_loop & TOP=$!; else echo "NOT RUN pod CPU/RSS: KUBECONFIG unset" | tee -a "$OUT/result.txt"; fi
trap '[ -n "$TOP" ] && kill "$TOP" 2>/dev/null' EXIT

rc=0
k6 run --quiet "$here/platform-surfaces.js" > "$OUT/platform-surfaces.log" 2>&1 || rc=1
cat "$OUT/platform-surfaces.txt" >> "$OUT/result.txt" 2>/dev/null || echo "FAIL platform-surfaces: no summary" >> "$OUT/result.txt"

hold=$(( $(echo "$JC_K6_DURATION" | sed -E 's/m$/*60/; s/s$//') ))
if [ -n "${LIVE_URL:-}" ]; then
  python3 "$here/streams.py" sse --url "$LIVE_URL" --readers 200 --hold "$hold" >> "$OUT/result.txt" 2>&1 || rc=1
else
  echo "NOT RUN sse: LIVE_URL unset" >> "$OUT/result.txt"
fi
if [ -n "${MQTT_HOST:-}" ]; then
  python3 "$here/streams.py" mqtt --host "$MQTT_HOST" --port "${MQTT_PORT:-8883}" --tls --devices 500 --rate 1 \
    --seconds "$hold" --topic "${MQTT_TOPIC:-load/{device}/attrs}" ${LANDED_URL:+--landed-url "$LANDED_URL"} \
    >> "$OUT/result.txt" 2>&1 || rc=1
else
  echo "NOT RUN mqtt: MQTT_HOST unset (needs a device identity of a scratch space)" >> "$OUT/result.txt"
fi
cat "$OUT/result.txt"
exit "$rc"
