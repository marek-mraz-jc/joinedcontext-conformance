#!/usr/bin/env bash
# Proof that the golden harness can go red (T-0077, PL-22).
#   1. the fixture pipeline passes its golden tests
#   2. a mapping that stops matching the golden output fails
#   3. a directory without a single test definition is an error, not a pass
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export JC_REPORTS_DIR="$work/reports"

cp -r "$here/fixtures/." "$work/pipelines"

fail() { echo "FAIL $*" >&2; exit 1; }

PIPELINES_DIR="$work/pipelines" "$here/run.sh" >"$work/good.log" 2>&1 \
  || fail "the fixture pipeline does not pass its own golden tests: $(cat "$work/good.log")"

# the mapping now reports every spot as free: the golden test must notice
sed -i 's/if this.occupied { "occupied" } else { "free" }/"free"/' "$work/pipelines/parking-mqtt/bento.yaml"
if PIPELINES_DIR="$work/pipelines" "$here/run.sh" >"$work/bad.log" 2>&1; then
  fail "a mapping that answers 'free' for an occupied spot passed the golden tests"
fi
grep -q "occupied" "$work/bad.log" || fail "the failure report does not name the value that drifted"

mkdir -p "$work/empty"
if PIPELINES_DIR="$work/empty" "$here/run.sh" >"$work/empty.log" 2>&1; then
  fail "a directory without a single golden test definition passed"
fi

echo "ok: the golden harness passes the fixture, fails a drifted mapping and refuses an empty tree"
