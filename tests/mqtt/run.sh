#!/usr/bin/env bash
# Eclipse Paho MQTT 3.1.1 and 5 interoperability tests against the edge broker as the platform
# ships it (T-4879, ADR-N-048): RMQTT at the digest of joinedcontext-deployment
# components/mqtt-brokers/images.yaml with the chart's listener (rmqtt.toml beside this file),
# gated by baseline.json. Needs docker and git; nothing is installed.
#
#   tests/mqtt/run.sh                  start the broker, run both versions, check the baseline
#   MQTT_HOST=h MQTT_PORT=p run.sh     run against a broker that is already up (a dev broker
#                                      through a port-forward), no container started
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
image=${RMQTT_IMAGE:-rmqtt/rmqtt:0.24.0@sha256:9d9496cfbe4c853d1246a4a151bab652898130b0a819ff69fbae9a786865a637}
# eclipse-paho/paho.mqtt.testing master of 2024-01-18; the suite has no releases.
paho_ref=9d7bb80bb8b9d9cfc0b52f8cb4c1916401281103
suite=$(mktemp -d)
trap 'rm -rf -- "${suite:?}"; [ -z "${started:-}" ] || docker rm -f "$started" >/dev/null' EXIT
git -C "$suite" init -q
git -C "$suite" fetch -q --depth 1 https://github.com/eclipse-paho/paho.mqtt.testing.git "$paho_ref"
git -C "$suite" checkout -q FETCH_HEAD

host=${MQTT_HOST:-127.0.0.1}
port=${MQTT_PORT:-}
if [ -z "$port" ]; then
  port=18831
  started=$(docker run -d --rm -p "127.0.0.1:$port:1883" -v "$here/rmqtt.toml:/etc/rmqtt/rmqtt.toml:ro" \
    "$image" -f /etc/rmqtt/rmqtt.toml)
  for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null && break; sleep 0.2; done
fi

rc=0
for version in 3.1.1 5; do
  python3 "$here/paho_interop.py" run "$suite/interoperability" "$version" "$host" "$port" \
    "$out/paho-$version.json" > "$out/paho-$version.log" 2>&1
  python3 "$here/paho_interop.py" check "$out/paho-$version.json" "$here/baseline.json" "$version" || rc=1
done
exit $rc
