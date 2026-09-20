#!/usr/bin/env bash
# Platform end-to-end journey runner (T-0331, PL-01, EP-01, EP-61, SP-09, AP-01).
# The shape checks run against the committed fixtures with no configuration; every stage that
# needs a running platform skips cleanly without the variable that names it (see README.md).
set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

set +e
python3 -m pytest "$here" -q -p no:cacheprovider --junitxml="$out/e2e.xml" "$@" 2>&1 | tee "$out/e2e.log"
rc=${PIPESTATUS[0]}
set -e
exit "$rc"
