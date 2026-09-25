#!/usr/bin/env bash
# The authorization matrix against a running Portal (T-2797, PF-52, AC-01): every cell of the
# Portal's tests/authz_matrix.yaml that dev can answer, as the demo people of each role.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
exec python3 -m pytest --junitxml="$out/junit.xml" -q -p no:cacheprovider "$@" "$here"
