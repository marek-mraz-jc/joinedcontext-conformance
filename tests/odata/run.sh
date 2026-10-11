#!/usr/bin/env bash
# OData conformance suite of a view's endpoint (T-3510, Antares surfaces-conformance.md section 4).
# Extra arguments go to pytest; the merge-request subset is `-k "abnf or metadata"`.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${ODATA_URL:?set ODATA_URL to the service root of view all, .../x/views/all/odata/v4}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
