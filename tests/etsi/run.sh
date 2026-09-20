#!/usr/bin/env bash
# Runs the ETSI NGSI-LD Robot suites of this folder against ${NGSILD_URL} (T-0055).
# Extra arguments are passed to robot, e.g.  jc-conformance etsi -- --include smoke
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
# NGSILD_EXPECTED_FAILURES names a quarantine list for the system under test (the gateway
# matrix has one, the broker matrix has none): robot's own verdict is then replaced by
# check_expected_failures.py, which goes red on any failure outside the list, on any listed
# case that passes, and on any entry past its date (docs/Testing/02-conformance-tests.md §1).
quarantine=${NGSILD_EXPECTED_FAILURES:-}
verdict=()
[ -z "$quarantine" ] || verdict=(--nostatusrc)
robot \
  --outputdir "$out" \
  --xunit xunit.xml \
  --variable "NGSILD_URL:${NGSILD_URL:?set NGSILD_URL to the NGSI-LD API root of the system under test}" \
  --variable "TENANT:${NGSILD_TENANT:-}" \
  --variable "TOKEN:${NGSILD_TOKEN:-}" \
  --variable "ORG_DOMAIN:${NGSILD_ORG_DOMAIN:-conformance}" \
  --variable "SPACE:${NGSILD_SPACE:-smoke}" \
  --variable "PROJECTED_URL:${NGSILD_PROJECTED_URL:-}" \
  --variable "FEDERATED_URL:${NGSILD_FEDERATED_URL:-}" \
  "${verdict[@]}" "$@" "$here"
[ -n "$quarantine" ] || exit 0
exec python3 "$here/../etsi-ttf/check_expected_failures.py" "$out/output.xml" "$quarantine"
