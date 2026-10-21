#!/usr/bin/env bash
# Anonymous NGSI-LD read smoke of a deployed Endpoint (T-5044): writes nothing, so it runs on prod.
# Extra arguments go to pytest.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${NGSILD_URL:?set NGSILD_URL to the NGSI-LD root of the Endpoint, .../api/endpoint/{slug}/ngsi-ld/v1}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
