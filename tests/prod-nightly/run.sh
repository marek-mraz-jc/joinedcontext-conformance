#!/usr/bin/env bash
# Nightly read-only standards run against production (T-4877), 01:00-04:00Z, one suite at a time.
#   OGC API Features ETS 1.8 (TEAM Engine, the image and baseline Antares CI uses), the read-only
#   OData cases of tests/odata (ABNF URL forms, $metadata; the NLstn suite needs its own reference
#   model and a Reseed write, so it stays in Antares CI), the STA read path (tests/sta; the official
#   ETS comes with T-4878), DCAT-AP 3.0.0 SHACL of the CKAN feed, RFC 7946 of OGC items, RFC 5545
#   of the iCal feeds a view serves. No credential: every call is anonymous, as a visitor's.
#   The ETSI NGSI-LD TTF suite never runs here (owner rule for prod and dev).
#
#   ANTARES_DIR   Antares checkout at the commit prod's broker runs (its baselines and parsers)
#   OUT           report folder (default ./prod-nightly-<date>)
#   API           https://api.smartcity.joinedcontext.com    CATALOGUE  https://opendata.smartcity.joinedcontext.com
#   ENDPOINTS     public Endpoint ids to test, space separated (default: endpoints.txt)
#   TE_USER, TE_PASSWORD   the TEAM Engine image's own public test account (ogctest), as tests/ogc/run.sh
set -uo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${ANTARES_DIR:?set ANTARES_DIR to an Antares checkout at the broker commit prod runs}"
api=${API:-https://api.smartcity.joinedcontext.com}
catalogue=${CATALOGUE:-https://opendata.smartcity.joinedcontext.com}
out=${OUT:-$PWD/prod-nightly-$(date -u +%Y%m%d)}
endpoints=${ENDPOINTS:-$(grep -v '^#' "$here/endpoints.txt")}
mkdir -p "$out"
export ANTARES_DIR
compare=("python3" "$here/compare.py")
report=$out/report.md
rc=0
note() { echo "$*" | tee -a "$report"; }
pause() { sleep "${PAUSE:-30}"; }   # the edge sees one address: a gap between suites keeps it under the per-IP budget

note "# Prod standards run $(date -u +%FT%TZ)"
note "Antares $(git -C "$ANTARES_DIR" rev-parse --short HEAD), endpoints: $endpoints"
"${compare[@]}" --self-test >/dev/null || { note "compare.py self-test failed"; exit 2; }

# OGC API Features: TEAM Engine on the host network, one run per Endpoint, every collection.
te_image=$(sed -n 's/^ *TE_IMAGE: *//p' "$ANTARES_DIR/.github/workflows/surfaces-conformance.yml" | head -1)
te=$(docker run -d --rm -p "127.0.0.1:${TE_PORT:-8089}:8080" "$te_image")
trap 'docker stop "$te" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 60); do
  curl -fsS -m 5 -u "${TE_USER:-ogctest}:${TE_PASSWORD:-ogctest}" -o /dev/null "localhost:${TE_PORT:-8089}/teamengine/rest/suites" && break
  sleep 5
done
note "## OGC API Features ETS ($te_image)"
for e in $endpoints; do
  landing="$api/api/endpoint/$e/ogc/features"
  iut=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$landing")
  curl -sS -m 3000 -u "${TE_USER:-ogctest}:${TE_PASSWORD:-ogctest}" -H 'Accept: application/xml' -o "$out/ogc-$e.xml" \
    "localhost:${TE_PORT:-8089}/teamengine/rest/suites/ogcapi-features-1.0/run?iut=$iut&noofcollections=-1"
  "${compare[@]}" ogc "$out/ogc-$e.xml" "$ANTARES_DIR/ci/ogc-compliance/ogc-public.json" "endpoint $e" >> "$report" || rc=1
  items=$(curl -fsS -m 30 -H 'Accept: application/json' "$landing/collections" | python3 -c 'import json,sys
for c in json.load(sys.stdin).get("collections", [])[:5]: print(c["id"])' 2>/dev/null)
  urls=(); for c in $items; do urls+=("$landing/collections/$c/items?limit=20"); done
  [ ${#urls[@]} -gt 0 ] && { "${compare[@]}" geojson "${urls[@]}" >> "$report" || rc=1; }
  pause
done
docker stop "$te" >/dev/null 2>&1

# OData: the read-only cases of tests/odata; its write cases stay off a public view.
note "## OData read cases (tests/odata -k 'abnf or metadata')"
for e in $endpoints; do
  if ODATA_URL="$api/api/endpoint/$e/odata/v4" JC_REPORTS_DIR=$out/odata-$e "$here/../odata/run.sh" \
    -p no:cacheprovider -k "abnf or metadata" > "$out/odata-$e.txt" 2>&1
  then note "- endpoint $e: $(tail -1 "$out/odata-$e.txt")"
  else note "- endpoint $e: FAILED $(grep -E '^FAILED' "$out/odata-$e.txt" | cut -c1-160 | tr '\n' ' ') $(tail -1 "$out/odata-$e.txt")"; rc=1; fi
  pause
done

# SensorThings read path (write methods are asserted rejected), where the Endpoint serves STA.
note "## SensorThings API 1.1 read path (tests/sta)"
for e in $endpoints; do
  sta="$api/api/endpoint/$e/sta/v1.1"
  [ "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$sta")" = 200 ] || { note "- endpoint $e: no STA"; continue; }
  if STA_URL=$sta JC_REPORTS_DIR=$out/sta-$e "$here/../sta/run.sh" -p no:cacheprovider > "$out/sta-$e.txt" 2>&1
  then note "- endpoint $e: $(tail -1 "$out/sta-$e.txt")"
  else note "- endpoint $e: FAILED $(tail -1 "$out/sta-$e.txt")"; rc=1; fi
  pause
done

# iCal, where a view serves Events.
note "## iCalendar (RFC 5545)"
served=0
for e in $endpoints; do
  ics="$api/api/endpoint/$e/ical/v1/events.ics"
  [ "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$ics")" = 200 ] || continue
  served=1; "${compare[@]}" ical "$ics" >> "$report" || rc=1
done
[ $served = 1 ] || note "- no tested Endpoint serves an iCal feed"

# DCAT-AP 3.0.0 SHACL over the catalogue's DCAT feed (first page).
note "## DCAT-AP 3.0.0 SHACL ($catalogue/catalog.ttl)"
curl -fsS -m 120 -o "$out/catalog.ttl" "$catalogue/catalog.ttl"
python3 - "$out/catalog.ttl" "$here/../../dcat/shapes/dcat-ap/3.0.0/dcat-ap-SHACL.ttl" >> "$report" <<'PY' || rc=1
import collections, sys
from pyshacl import validate
from rdflib import Graph, Namespace
SH = Namespace("http://www.w3.org/ns/shacl#")
conforms, results, _ = validate(Graph().parse(sys.argv[1]), shacl_graph=Graph().parse(sys.argv[2]), inference="none")
by = collections.Counter((str(results.value(r, SH.resultSeverity)).rsplit("#", 1)[-1],
                          str(results.value(r, SH.resultPath) or results.value(r, SH.sourceShape)).rsplit("/", 1)[-1])
                         for r in results.subjects(SH.resultSeverity, None))
print(f"- conforms: {conforms}; {sum(by.values())} results")
for (severity, path), n in by.most_common(15):
    print(f"  - {severity} {path}: {n}")
sys.exit(0 if conforms else 1)
PY

note "## Verdict: $([ $rc = 0 ] && echo GREEN || echo RED)"
exit $rc
