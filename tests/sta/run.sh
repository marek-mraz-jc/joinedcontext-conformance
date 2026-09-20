#!/usr/bin/env bash
# SensorThings API v1.1 Sensing Profile read path (T-0059, TS-08, EP-12, EP-13).
# Extra arguments go to pytest.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${STA_URL:?set STA_URL to the STA root of the endpoint, .../api/endpoint/{slug}/sta/v1.1}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
