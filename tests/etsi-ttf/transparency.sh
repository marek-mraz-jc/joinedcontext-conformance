#!/usr/bin/env bash
# Gateway transparency: the ETSI TTF suite run twice, once at the broker and once through the
# Context Gateway under an unrestricted administrative policy (T-0056, TS-06, R15).
#   BROKER_URL    NGSI-LD API root of the broker's internal listener
#   GATEWAY_URL   NGSI-LD API root of the same data through the gateway, e.g. /cs/{space}/ngsi-ld/v1
# Extra arguments are passed to robot in both legs.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

: "${BROKER_URL:?set BROKER_URL to the NGSI-LD API root of the broker itself}"
: "${GATEWAY_URL:?set GATEWAY_URL to the same data through the Context Gateway}"

# one checkout for both legs; run.sh would otherwise clone the suite once per reports directory
export TTF_DIR=${TTF_DIR:-$out/ngsi-ld-test-suite}

for leg in direct:"$BROKER_URL" gateway:"$GATEWAY_URL"; do
  name=${leg%%:*}
  url=${leg#*:}
  echo "=== $name leg against $url"
  # the quarantine gate of run.sh may fail a leg; the comparison below is the verdict that
  # belongs to this task, so record the exit code and keep going
  JC_REPORTS_DIR="$out/$name" NGSILD_URL="$url" bash "$here/run.sh" "$@" \
    && echo "=== $name leg passed its own quarantine gate" \
    || echo "=== $name leg exited $? (see $out/$name)"
  [ -f "$out/$name/output.xml" ] || { echo "the $name leg produced no report" >&2; exit 1; }
done

exec python3 "$here/compare_transparency.py" "$out/direct/output.xml" "$out/gateway/output.xml"
