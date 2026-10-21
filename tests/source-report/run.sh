#!/usr/bin/env bash
# Source vs space of every snapshot pipeline on dev (T-4892), nightly. Reads PORTAL_URL,
# PORTAL_TOKEN and SOURCE_REPORT_PROJECTS (`. tests/dev.env.sh source-report`); extra arguments go
# to check.py.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
exec python3 "$here/check.py" --junit "$out/junit.xml" "$@"
