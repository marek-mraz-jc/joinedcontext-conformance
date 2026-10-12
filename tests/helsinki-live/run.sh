#!/usr/bin/env bash
# The Helsinki live feed, end to end (T-3907): five minutes of HFP, endpoint reads and the
# endpoint's WebSocket, matched bus by bus. Reads HELSINKI_ENDPOINT_URL and HELSINKI_TOKEN
# (`. tests/dev.env.sh helsinki-live`); extra arguments go to live.py.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
exec python3 "$here/live.py" --junit "$out/junit.xml" "$@"
