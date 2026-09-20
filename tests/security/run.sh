#!/usr/bin/env bash
# Runs the Security & Policy bypass test suites against ${SPACE_URL} (T-0082, T-0083, T-0084).
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
exec python3 -m pytest \
  --junitxml="$out/junit.xml" \
  -q \
  "$@" "$here"
