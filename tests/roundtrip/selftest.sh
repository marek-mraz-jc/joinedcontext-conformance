#!/usr/bin/env bash
# The roundtrip suite against a local broker (T-5914): the Antares image dev runs, memory store,
# every surface dev mounts, one view `rt` over the fixture schema (broker/). Three runs:
#   1. write mode through the view: every served surface green, the test Entity gone after;
#   2. read mode over an Entity seeded at the tenant root (outside the test id prefix): green;
#   3. the deliberate break: a proxy rounds OData's pm10 to an integer: write mode red on OData alone.
#   ANTARES_IMAGE  the broker image (default: the antares-broker-jc digest dev pinned 2026-10-28)
#   PORT           host port of the broker (default 19190)
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
image=${ANTARES_IMAGE:-ghcr.io/marek-mraz-jc/antares-broker-jc@sha256:67f5833d6b61dee7d343bb2dd4ca47c4831689609ec7caa1c364f4adc8e6de55}
port=${PORT:-19190}
broker=http://localhost:$port
out=$(mktemp -d)
name=roundtrip-selftest-$$
proxy=""
trap '[ -z "$proxy" ] || kill "$proxy" 2>/dev/null || true; docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "${out:?}"' EXIT
python3 "$here/roundtrip.py" --self-test
docker run -d --rm --name "$name" -p "127.0.0.1:$port:9090" -e ANTARES_STORE=memory \
  -e ANTARES_SCHEMA_MAX_BYTES=65536 \
  -e ANTARES_API_SURFACES=admin,views,mcp,access,ws,ws-views,ogc,linkml,odata,sta "$image" >/dev/null
for _ in $(seq 1 60); do curl -fsS -m 2 -o /dev/null "$broker/q/health" 2>/dev/null && break; sleep 1; done
put() { curl -fsS -o /dev/null -X PUT -H 'content-type: application/json' --data-binary "@$here/broker/$2" "$broker$1"; }
put /q/schemas/roundtrip schema.json
put /q/views/rt view.json
# The first create makes the tenant (a view over a tenant nobody wrote answers 404): seed read mode's Entity first.
seeded=urn:ngsi-ld:AirQualityObserved:seeded-roundtrip
curl -fsS -o /dev/null -X POST -H 'content-type: application/json' -H 'NGSILD-Tenant: roundtrip' "$broker/ngsi-ld/v1/entities" -d \
  '{"id":"'"$seeded"'","type":"AirQualityObserved","name":{"type":"Property","value":"seeded"},
    "pm10":{"type":"Property","value":12.5,"unitCode":"GQ","observedAt":"2026-10-28T09:00:00Z"},
    "location":{"type":"GeoProperty","value":{"type":"Point","coordinates":[17.1,48.1]}}}'

echo "== 1. write mode"
python3 "$here/roundtrip.py" --endpoint "$broker/x/views/rt" --out "$out/write"
! grep -q $'\tnot served\t' "$out/write/cases.tsv" || { echo "a surface of the local broker was not served" >&2; exit 1; }

echo "== 2. read mode"
python3 "$here/roundtrip.py" --endpoint "$broker/x/views/rt" --read "$seeded" --out "$out/read"

echo "== 3. the deliberate break: OData rounds pm10 (broker/rounding_proxy.py in front of the broker)"
python3 "$here/broker/rounding_proxy.py" "$((port + 1))" "$broker" &
proxy=$!
for _ in $(seq 1 20); do curl -fsS -m 2 -o /dev/null "http://localhost:$((port + 1))/q/health" 2>/dev/null && break; sleep 0.5; done
if python3 "$here/roundtrip.py" --endpoint "http://localhost:$((port + 1))/x/views/rt" --out "$out/broken"; then
  echo "the break went green: the suite does not see a rounded float" >&2; exit 1
fi
grep -q 'current OData.*fail.*pm10.value: want 21.5 got 22' "$out/broken/cases.tsv" \
  || { echo "the red run did not name OData's rounded pm10" >&2; cat "$out/broken/cases.tsv" >&2; exit 1; }
grep -q 'failures="1"' "$out/broken/junit.xml" || { echo "the break turned more than OData red" >&2; exit 1; }
echo "roundtrip selftest: ok"
