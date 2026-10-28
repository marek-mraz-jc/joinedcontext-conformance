#!/usr/bin/env bash
# The scheduled prod load run (T-5901), started by the prod deployer's cron on Sunday 05:00Z
# (`0 5 * * 0`) after the run is announced in AI_shared_folder.md. It refuses to start inside the
# 01:00-04:00Z nightly window (T-4877, the 02:30Z backup), records `kubectl top nodes` before and
# after, remote-writes k6 metrics through a port-forward to the cluster Prometheus, and stops k6
# when the cluster's Postgres stays above JC_LOAD_PG_CORES (default 5) for a minute. k6 stops
# itself on p95 > 2 s or errors > 2 % (api-mix-100rps.js).
# Prometheus accepts a remote write only during the run: the script switches the receiver on in
# the Prometheus resource and off again on exit (the deployment keeps it off, so no in-cluster
# caller such as Grafana's data proxy can write series between runs). Each switch restarts
# Prometheus once (about a minute; its WAL is on the volume).
#
#   KUBECONFIG          the target cluster (read-only use: top, port-forward)
#   JC_PROM_NAMESPACE   the Prometheus namespace, e.g. smartcity-prometheus
#   JC_PROM_NAME        the Prometheus resource (default prometheus-prometheus)
#   JC_LOAD_PROFILES    profiles in order (default steady)
#   and load.sh's JC_K6_REQUESTS, JC_LOAD_TARGET, JC_DEPLOYMENT_SHA, JC_K6_TOKEN_FILE
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${KUBECONFIG:?set KUBECONFIG to the target cluster}"
: "${JC_PROM_NAMESPACE:?set JC_PROM_NAMESPACE to the Prometheus namespace}"
limit=${JC_LOAD_PG_CORES:-5}
port=${JC_PROM_PORT:-19090}

hour=$((10#$(date -u +%H)))
if [ "$hour" -ge 1 ] && [ "$hour" -lt 4 ]; then
  echo "refused: 01:00-04:00Z is the nightly window (T-4877) and the 02:30Z backup" >&2
  exit 2
fi

name=${JC_PROM_NAME:-prometheus-prometheus}
receiver() {
  kubectl -n "$JC_PROM_NAMESPACE" patch prometheus "$name" --type merge -p "{\"spec\":{\"enableRemoteWriteReceiver\":$1}}" >/dev/null
  sleep 5
  kubectl -n "$JC_PROM_NAMESPACE" rollout status "statefulset/prometheus-$name" --timeout=300s >/dev/null
}
forward=""
watch=""
trap 'kill "$forward" 2>/dev/null || true; [ -z "$watch" ] || kill "$watch" 2>/dev/null || true; receiver false || echo "the remote-write receiver is still ON: kubectl -n $JC_PROM_NAMESPACE patch prometheus $name --type merge -p {\"spec\":{\"enableRemoteWriteReceiver\":false}}" >&2' EXIT
receiver true
kubectl -n "$JC_PROM_NAMESPACE" port-forward svc/prometheus-prometheus "$port:9090" >/dev/null 2>&1 &
forward=$!
prom="http://127.0.0.1:$port"
for _ in $(seq 20); do curl -fsS "$prom/-/ready" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$prom/-/ready" >/dev/null || { echo "Prometheus not reachable through the port-forward" >&2; exit 1; }
export K6_PROMETHEUS_RW_SERVER_URL="$prom/api/v1/write"

pg_cores() {
  curl -fsS --get "$prom/api/v1/query" \
    --data-urlencode 'query=sum(rate(container_cpu_usage_seconds_total{container="postgres"}[1m]))' |
    jq -r '.data.result[0].value[1] // "0"'
}

status=0
for profile in ${JC_LOAD_PROFILES:-steady}; do
  evidence="${JC_LOAD_EVIDENCE:-$here/../../evidence/load}/$(date -u +%Y-%m-%d)-$JC_LOAD_TARGET-$profile"
  mkdir -p "$evidence"
  kubectl top nodes > "$evidence/top-before.txt" 2>&1 || true
  JC_K6_PROFILE=$profile "$here/load.sh" &
  k6pid=$!
  # The Postgres guard: two readings over the limit 30 s apart stop the run.
  (
    over=0
    while kill -0 "$k6pid" 2>/dev/null; do
      sleep 30
      if awk -v c="$(pg_cores)" -v l="$limit" 'BEGIN { exit !(c > l) }'; then
        over=$((over + 1))
      else
        over=0
      fi
      if [ "$over" -ge 2 ]; then
        echo "Postgres above $limit cores for a minute: stopping the run" >&2
        pkill -INT -P "$k6pid" k6 || true
        exit 0
      fi
    done
  ) &
  watch=$!
  wait "$k6pid" || status=$?
  kill "$watch" 2>/dev/null || true
  wait "$watch" 2>/dev/null || true
  watch=""
  kubectl top nodes > "$evidence/top-after.txt" 2>&1 || true
  [ "$status" -eq 0 ] || break
done
exit "$status"
