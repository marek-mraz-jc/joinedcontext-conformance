#!/usr/bin/env bash
# k6 performance budgets against a running gateway (T-0065, T-0066, T-0067, TS-22, EP-28, EP-41, EP-44, OPS-18).
# Supported JC_K6_SCRIPT values:
#   gateway-latency-load.js (default) - latency and throughput benchmark (T-0065)
#   gateway-endurance.js              - 30-minute endurance and memory soak (T-0066)
#   bulk-export.js                    - high-volume streaming export benchmark (T-0067)
#   transport_endpoint_load.js        - sustained load on one endpoint's representations (T-0332)
# Extra arguments go to `k6 run`, e.g.  jc-conformance k6 -- --vus 10
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

if [ "${JC_K6_SCRIPT:-}" = "bulk-export.js" ]; then
  : "${EXPORT_URL:?set EXPORT_URL to the root of an endpoint, e.g. https://host/api/endpoint/<slug>}"
elif [ "${JC_K6_SCRIPT:-}" = "transport_endpoint_load.js" ]; then
  : "${ENDPOINT_URL:?set ENDPOINT_URL to the root of an endpoint, e.g. https://host/api/endpoint/<slug>}"
  [ -n "${BROKER_URL:-}" ] || echo "BROKER_URL unset: the EP-28 overhead budget will not be measured" >&2
  [ -n "${JC_K6_BURST_RATE:-}" ] || echo "JC_K6_BURST_RATE unset: the EP-20 rate limiter will not be proven" >&2
else
  : "${GATEWAY_URL:?set GATEWAY_URL to the NGSI-LD API root of a space, e.g. https://host/cs/ovzdusie/ngsi-ld/v1}"
  case "${JC_K6_SCRIPT:-gateway-latency-load.js}" in
    gateway-latency-load.js)
      [ -n "${BROKER_URL:-}" ] || echo "BROKER_URL unset: the EP-28 overhead budget will not be measured" >&2 ;;
    gateway-endurance.js)
      [ -n "${GATEWAY_METRICS_URL:-}" ] || echo "GATEWAY_METRICS_URL unset: the TS-22 memory budget will not be measured" >&2 ;;
  esac
fi

export JC_REPORTS_DIR="$out"
exec k6 run "$@" "$here/${JC_K6_SCRIPT:-gateway-latency-load.js}"
