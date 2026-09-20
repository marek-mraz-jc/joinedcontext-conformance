#!/usr/bin/env bash
# OGC API - Features conformance (T-0057, T-0058, TS-07, EP-29…EP-40).
#   1. the query-parameter and CQL2 suite of this folder (pytest)
#   2. the official ets-ogcapi-features10 Abstract Test Suite, driven through TEAM Engine
# Extra arguments go to pytest.
#   OGC_LANDING_URL   landing page of the endpoint, .../api/endpoint/{slug}/ogc/features
#   OGC_COLLECTION    collection to exercise (default: the first one advertised)
#   OGC_TOKEN         bearer token
#   TE_BASE_URL       an already running TEAM Engine; without it one is started with docker
#   OGC_SKIP_ATS=1    run only the pytest suite (no docker available)
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

: "${OGC_LANDING_URL:?set OGC_LANDING_URL to the landing page of the OGC Features endpoint}"

rc=0
python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here" || rc=1

[ "${OGC_SKIP_ATS:-}" = 1 ] && { echo "OGC_SKIP_ATS=1: the Abstract Test Suite was not run" >&2; exit "$rc"; }

# ets-ogcapi-features10 is a TEAM Engine web application: start it, ask it for a run, read the
# TestNG document it answers with. Pinned by digest like every other image we depend on.
# ETS 1.9, the version the suite descriptor reports; only :latest is published, so the digest is
# the pin. Re-pin deliberately when the ETS is upgraded — a moving conformance suite is not a gate.
TE_IMAGE=${TE_IMAGE:-ogccite/ets-ogcapi-features10@sha256:a1c1345acff1f671a6ca6aefc36a85551c855223973aff3d40c4e75611c7e55e}
base=${TE_BASE_URL:-}
container=""
if [ -z "$base" ]; then
  command -v docker >/dev/null || { echo "no docker and no TE_BASE_URL: cannot run the ATS" >&2; exit 2; }
  port=${TE_PORT:-8081}
  container=$(docker run -d --rm -p "$port:8080" "$TE_IMAGE")
  trap 'docker stop "$container" >/dev/null 2>&1 || true' EXIT
  base="http://localhost:$port"
fi

for _ in $(seq 1 60); do
  curl -fsS -m 5 -u "${TE_USER:-ogctest}:${TE_PASSWORD:-ogctest}" "$base/teamengine/rest/suites" >/dev/null 2>&1 && break
  sleep 5
done

iut=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$OGC_LANDING_URL")
curl -fsS -m "${TE_TIMEOUT:-3600}" -u "${TE_USER:-ogctest}:${TE_PASSWORD:-ogctest}" \
  -H "Accept: application/xml" \
  "$base/teamengine/rest/suites/ogcapi-features-1.0/run?iut=$iut&noofcollections=${OGC_COLLECTIONS:-3}" \
  -o "$out/testng-results.xml"

python3 "$here/check_ogc_results.py" "$out/testng-results.xml" || rc=1
exit "$rc"
