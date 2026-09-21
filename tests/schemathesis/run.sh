#!/usr/bin/env bash
# Schemathesis contract fuzzing of the Portal API (T-0063, TS-09).
# Extra arguments go to `schemathesis run`, e.g.  jc-conformance schemathesis -- --include-path-regex projects
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
cd "$out"   # schemathesis writes .schemathesis/ and .hypothesis/ into the working directory

url=${PORTAL_URL:?set PORTAL_URL to the Portal API root of the system under test}
url=${url%/}
schema=${PORTAL_OPENAPI_URL:-$url/api/v1/openapi.json}

export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
export SCHEMATHESIS_HOOKS=jc_checks

# TS-09 asks for the built-in conformance checks plus the two contract checks of jc_checks.py;
# 500 examples per operation is the floor the task sets.
checks="all,ui05_error_is_problem_json,ts09_no_internal_detail_leak"
portal_args=(
  --url "$url"
  --max-examples "${JC_SCHEMATHESIS_EXAMPLES:-500}"
  --continue-on-failure
  --report junit
  --report-dir "$out"
)
if [ -n "${JC_SCHEMATHESIS_SEED:-}" ]; then portal_args+=(--seed "$JC_SCHEMATHESIS_SEED"); fi

status=0

# One run per demo role whose token is set (T-1658): PORTAL_TOKEN_VIEWER, _STEWARD, _APPROVER.
# Each run writes its own junit-portal-<role>.xml, which is the list of operations that role
# reached, and a viewer run fails on any accepted write (pf50_viewer_never_writes, PF-50).
# Without any role token, one run with PORTAL_TOKEN (or none) as before.
roles=()
for role in viewer steward approver; do
  var="PORTAL_TOKEN_${role^^}"
  if [ -n "${!var:-}" ]; then roles+=("$role"); fi
done

if [ "${#roles[@]}" -gt 0 ]; then
  for role in "${roles[@]}"; do
    var="PORTAL_TOKEN_${role^^}"
    echo "Running Portal API schemathesis fuzzing as the ${role}..."
    JC_SCHEMATHESIS_ROLE="$role" schemathesis run "${portal_args[@]}" \
      --checks "${checks},pf50_viewer_never_writes" \
      --report-junit-path "$out/junit-portal-${role}.xml" \
      --header "Authorization: Bearer ${!var}" "$@" "$schema" || status=$?
  done
else
  if [ -n "${PORTAL_TOKEN:-}" ]; then portal_args+=(--header "Authorization: Bearer $PORTAL_TOKEN"); fi
  echo "Running Portal API schemathesis fuzzing..."
  schemathesis run "${portal_args[@]}" --checks "$checks" --report-junit-path "$out/junit.xml" \
    "$@" "$schema" || status=$?
fi

if [ -n "${GATEWAY_OPENAPI_URL:-}" ]; then
  echo "Running Context Gateway management schemathesis fuzzing against $GATEWAY_OPENAPI_URL..."
  gw_schema="$GATEWAY_OPENAPI_URL"
  gw_url="${GATEWAY_URL:-${gw_schema%/api/*}}"
  gw_args=(
    --url "$gw_url"
    --checks "all,ui05_error_is_problem_json,ts09_no_internal_detail_leak"
    --max-examples "${JC_SCHEMATHESIS_EXAMPLES:-500}"
    --continue-on-failure
    --report junit
    --report-dir "$out"
    --report-junit-path "$out/junit-gateway.xml"
  )
  if [ -n "${GATEWAY_TOKEN:-}" ]; then gw_args+=(--header "Authorization: Bearer $GATEWAY_TOKEN"); fi
  if [ -n "${JC_SCHEMATHESIS_SEED:-}" ]; then gw_args+=(--seed "$JC_SCHEMATHESIS_SEED"); fi
  schemathesis run "${gw_args[@]}" "$@" "$gw_schema" || status=$?
fi

if [ -n "${GATEWAY_URL:-}" ]; then
  echo "Running Context Gateway endpoint surface contract tests against $GATEWAY_URL..."
  python3 -m pytest --junitxml="$out/junit-endpoints.xml" -q "$here/test_gateway_endpoints.py" || status=$?
fi

exit "$status"
