#!/usr/bin/env bash
# The standards suite on demand (T-5044): every chosen suite against every chosen Endpoint of dev or
# prod, one verdict per (Endpoint, suite) in $OUT/results.tsv, rendered by summary.py. The workflow
# .github/workflows/standards.yml runs it; it runs the same from a shell.
#
#   TARGET       dev | prod (prod: anonymous, read-only, paced; a token is refused)
#   ENDPOINTS    Endpoint ids, space separated (default: endpoints-dev.txt, or prod-nightly/endpoints.txt)
#   SUITES       all, or any of: ogc sta odata ngsi-ld ical ws mcp (space or comma separated)
#   OUT          report folder (default ./standards-<target>-<UTC time>)
#   ANTARES_DIR  Antares checkout: the OGC ETS image and baseline, the run.sh preamble
#   PAUSE        seconds between two suites (default 30 on prod, 5 on dev): the edge sees one address
#   Dev write cases, only with all three set: NGSILD_TOKEN (the CI service account's bearer token),
#   WRITE_ENDPOINT (a dev write Endpoint), NGSILD_ORG_DOMAIN and NGSILD_SPACE for smoke.robot's ids.
set -uo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
tests=$(cd -- "$here/.." && pwd)
target=${TARGET:-dev}
case $target in
  dev) api=https://api.dev.joinedcontext.com; list=$here/endpoints-dev.txt; pause_default=5 ;;
  prod) api=https://api.smartcity.joinedcontext.com; list=$tests/prod-nightly/endpoints.txt; pause_default=30 ;;
  *) echo "TARGET is dev or prod, not '$target'" >&2; exit 2 ;;
esac
api=${API:-$api}
if [ "$target" = prod ] && [ -n "${NGSILD_TOKEN:-}" ]; then
  echo "a prod run is anonymous: unset NGSILD_TOKEN" >&2; exit 2
fi
endpoints=${ENDPOINTS:-$(grep -v '^#' "$list")}
for e in $endpoints; do
  [[ $e =~ ^[a-z0-9-]{1,64}$ ]] || { echo "not an Endpoint id: '$e'" >&2; exit 2; }
done
suites=${SUITES:-all}
suites=${suites//,/ }
[ "$suites" = all ] && suites="ngsi-ld ogc odata sta ical ws mcp"
for s in $suites; do
  case $s in ogc|sta|odata|ngsi-ld|ical|ws|mcp) ;; *) echo "unknown suite '$s'" >&2; exit 2 ;; esac
done
out=${OUT:-$PWD/standards-$target-$(date -u +%Y%m%dT%H%MZ)}
mkdir -p "$out"
results=$out/results.tsv
printf 'endpoint\tsuite\tverdict\tdetail\tlog\n' > "$results"
pause=${PAUSE:-$pause_default}
rc=0

record() {  # endpoint suite verdict detail log
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(tr '\t\n' '  ' <<< "$4" | cut -c1-300)" "$5" >> "$results"
  [ "$3" = fail ] && rc=1
  echo "$1 $2: $3 $4"
}

status() { curl -s -m 20 -o /dev/null -w '%{http_code}' "$@"; }

served() {  # the surface root of suite $2 on Endpoint URL $1 answers as it should
  case $2 in
    ngsi-ld) [ "$(status "$1/ngsi-ld/v1/types")" = 200 ] ;;
    ogc) [ "$(status "$1/ogc/features")" = 200 ] ;;
    odata) [ "$(status "$1/odata/v4")" = 200 ] ;;
    sta) [ "$(status "$1/sta/v1.1")" = 200 ] ;;
    ical) [ "$(status "$1/ical/v1/events.ics")" = 200 ] ;;
    ws) [ "$(status -H 'Accept: application/json' "$1/ws")" = 426 ] ;;
    mcp) [ "$(status -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":0,"method":"ping"}' "$1/mcp")" = 200 ] ;;
  esac
}

pytest_run() {  # endpoint suite log -- pytest args; the verdict is pytest's, the detail its last line
  local e=$1 s=$2 log=$3; shift 4
  if python3 -m pytest -p no:cacheprovider -q -rfs --junitxml="${log%.txt}.xml" "$@" > "$log" 2>&1
  then record "$e" "$s" pass "$(tail -1 "$log")" "${log#"$out"/}"
  else record "$e" "$s" fail "$(grep -m3 -E '^FAILED|^ERROR' "$log" | cut -c1-120) $(tail -1 "$log")" "${log#"$out"/}"; fi
}

nightly_run() {  # endpoint suite dir: one suite of prod-nightly/run.sh, its report lines the detail
  local e=$1 s=$2 dir=$3
  if API=$api SUITES=$s ENDPOINTS=$e OUT=$dir PAUSE=0 "$tests/prod-nightly/run.sh" > "$dir/run.txt" 2>&1
  then record "$e" "$s" pass "$(grep -m2 '^- ' "$dir/report.md")" "${dir#"$out"/}/report.md"
  else record "$e" "$s" fail "$(grep -m3 -E '^- |^  - REGRESSION|FAILED' "$dir/report.md") $(tail -1 "$dir/run.txt")" "${dir#"$out"/}/report.md"; fi
}

for e in $endpoints; do
  ep=$api/api/endpoint/$e
  for s in $suites; do
    dir=$out/$e/$s
    mkdir -p "$dir"
    if ! served "$ep" "$s"; then
      record "$e" "$s" n/a "the Endpoint does not serve $s" ""
      continue
    fi
    case $s in
      ogc|odata|sta) nightly_run "$e" "$s" "$dir" ;;
      ical) ICAL_URL=$ep/ical/v1/events.ics ICAL_NGSILD_URL=$ep/ngsi-ld/v1 \
              pytest_run "$e" "$s" "$dir/pytest.txt" -- "$tests/ical" -k live ;;
      ngsi-ld)
        NGSILD_URL=$ep/ngsi-ld/v1 pytest_run "$e" "$s" "$dir/pytest.txt" -- "$tests/ngsild-read"
        if [ "$target" = dev ] && [ "$e" = "${WRITE_ENDPOINT:-}" ] && [ -n "${NGSILD_TOKEN:-}" ]; then
          # The ETSI smoke suite writes its fixtures: dev write Endpoint and service account only, never TTF.
          if NGSILD_URL=$ep/ngsi-ld/v1 NGSILD_TOKEN=$NGSILD_TOKEN JC_REPORTS_DIR=$dir/etsi-smoke \
            NGSILD_EXPECTED_FAILURES=$tests/etsi/expected_failures_gateway.json \
            "$tests/etsi/run.sh" --include smoke > "$dir/etsi-smoke.txt" 2>&1
          then record "$e" "ngsi-ld etsi smoke" pass "$(grep -m1 -E 'tests?, ' "$dir/etsi-smoke.txt")" "${dir#"$out"/}/etsi-smoke"
          else record "$e" "ngsi-ld etsi smoke" fail "$(grep -m1 -E 'tests?, ' "$dir/etsi-smoke.txt") $(tail -1 "$dir/etsi-smoke.txt")" "${dir#"$out"/}/etsi-smoke"; fi
        fi ;;
      ws)
        GATEWAY_WS_URL=${ep/https:/wss:}/ws pytest_run "$e" "$s" "$dir/pytest.txt" -- "$tests/ws/test_gateway_ws.py"
        if [ "$target" = dev ] && [ "$e" = "${WRITE_ENDPOINT:-}" ] && [ -n "${NGSILD_TOKEN:-}" ]; then
          GATEWAY_WS_URL=${ep/https:/wss:}/ws GATEWAY_WS_TOKEN=$NGSILD_TOKEN GATEWAY_WS_WRITE=1 \
            pytest_run "$e" "ws write" "$dir/pytest-write.txt" -- "$tests/ws/test_gateway_ws.py" -k write
        fi ;;
      mcp) ENDPOINT_MCP_URL=$ep/mcp pytest_run "$e" "$s" "$dir/pytest.txt" -- "$tests/mcp/test_mcp_anonymous_read_only.py" ;;
    esac
    sleep "$pause"
  done
done
python3 "$here/summary.py" "$results" "$target" "$api" > "$out/summary.md"
cat "$out/summary.md"
exit $rc
