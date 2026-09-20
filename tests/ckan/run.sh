#!/usr/bin/env bash
# CKAN and DCAT-AP publication conformance runner (T-0320, EP-27, EP-61…EP-67).
# The DCAT-AP checks run against the fixture with no configuration; the endpoint and CKAN
# checks need ENDPOINT_URL and CKAN_URL and skip cleanly without them.
set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

set +e
python3 -m pytest "$here" -q -p no:cacheprovider --junitxml="$out/ckan.xml" "$@" 2>&1 | tee "$out/ckan.log"
rc=${PIPESTATUS[0]}
set -e
exit "$rc"
