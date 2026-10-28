#!/usr/bin/env bash
# One named load run of the API mix with its record (T-5901): the profile against a request list,
# metrics remote-written to Prometheus when K6_PROMETHEUS_RW_SERVER_URL is set (the Grafana
# dashboard "k6 load" reads them by testid), and the run's evidence under
# evidence/load/<date>-<target>-<profile>/ for a commit: the k6 summary, the per-standard and
# per-kind table, and run.json naming the conformance sha, the deployment sha and the request
# list's sha256. The request list and the summary carry no url parameters beyond the public
# catalogue's, and no token.
#
#   JC_K6_PROFILE      steady | closed | knee | soak (api-mix-100rps.js)
#   JC_K6_REQUESTS     the request list (make_requests.py), e.g. tests/k6/lists/prod.tsv
#   JC_LOAD_TARGET     a short name of the stack under load: prod or dev
#   JC_DEPLOYMENT_SHA  the joinedcontext-deployment commit the target runs
#   JC_K6_TOKEN_FILE   optional: a file holding the loadtest ServiceAccount's access token
#                      (read here, passed to k6 in the environment, never echoed)
#   JC_LOAD_EVIDENCE   optional: where the evidence goes (default <repo>/evidence/load)
# Extra arguments go to `k6 run`.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$here/../.." && pwd)

: "${JC_K6_PROFILE:?set JC_K6_PROFILE to steady, closed, knee or soak}"
: "${JC_K6_REQUESTS:?set JC_K6_REQUESTS to the request list, e.g. tests/k6/lists/prod.tsv}"
: "${JC_LOAD_TARGET:?set JC_LOAD_TARGET to prod or dev}"
: "${JC_DEPLOYMENT_SHA:?set JC_DEPLOYMENT_SHA to the deployment commit the target runs}"
case "$JC_LOAD_TARGET" in prod | dev) ;; *) echo "JC_LOAD_TARGET '$JC_LOAD_TARGET': prod or dev" >&2; exit 2 ;; esac
case "$JC_K6_PROFILE" in steady | closed | knee | soak) ;; *) echo "JC_K6_PROFILE '$JC_K6_PROFILE': steady, closed, knee or soak" >&2; exit 2 ;; esac
[[ "$JC_DEPLOYMENT_SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "JC_DEPLOYMENT_SHA '$JC_DEPLOYMENT_SHA': a commit sha" >&2; exit 2; }
[ -r "$JC_K6_REQUESTS" ] || { echo "$JC_K6_REQUESTS: no such request list" >&2; exit 2; }

if [ -n "${JC_K6_TOKEN_FILE:-}" ]; then
  [ -r "$JC_K6_TOKEN_FILE" ] || { echo "JC_K6_TOKEN_FILE: not readable" >&2; exit 2; }
  JC_K6_TOKEN=$(tr -d '\r\n' < "$JC_K6_TOKEN_FILE")
  export JC_K6_TOKEN
fi

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
testid="$(date -u +%Y%m%dT%H%M)-$JC_LOAD_TARGET-$JC_K6_PROFILE"
out="${JC_LOAD_EVIDENCE:-$repo/evidence/load}/$(date -u +%Y-%m-%d)-$JC_LOAD_TARGET-$JC_K6_PROFILE"
mkdir -p "$out"
export JC_REPORTS_DIR="$out" JC_K6_TESTID="$testid"

outputs=()
if [ -n "${K6_PROMETHEUS_RW_SERVER_URL:-}" ]; then
  export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(95),p(99),max}"
  outputs=(-o experimental-prometheus-rw)
fi

status=0
k6 run "${outputs[@]}" --summary-export "$out/k6-summary.json" "$@" "$here/api-mix-100rps.js" || status=$?
# handleSummary's full metric dump is the same data as k6-summary.json: keep one.
rm -f "$out/summary.json"

jq -n \
  --arg testid "$testid" --arg profile "$JC_K6_PROFILE" --arg target "$JC_LOAD_TARGET" \
  --arg started "$started" --arg ended "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg conformance "$(git -C "$repo" rev-parse HEAD)" --arg deployment "$JC_DEPLOYMENT_SHA" \
  --arg list "$(basename "$JC_K6_REQUESTS")" --arg list_sha256 "$(sha256sum "$JC_K6_REQUESTS" | cut -d' ' -f1)" \
  --argjson exit "$status" --argjson authenticated "$([ -n "${JC_K6_TOKEN:-}" ] && echo true || echo false)" \
  '{testid: $testid, profile: $profile, target: $target, started: $started, ended: $ended,
    conformance_sha: $conformance, deployment_sha: $deployment, requests: $list,
    requests_sha256: $list_sha256, authenticated: $authenticated, k6_exit: $exit,
    verdict: (if $exit == 0 then "pass" elif $exit == 99 then "thresholds red" else "error" end)}' \
  > "$out/run.json"
echo "evidence: $out (testid $testid, k6 exit $status)"
exit "$status"
