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
# The load generator's token (T-5921), when JC_LOADTEST_CLIENT is set: the realm keeps that client
# disabled, so the script enables it with kcadm inside the Keycloak pod (the admin password stays
# in the pod's environment), takes one client_credentials token with the client's secret from its
# Secret (sent on stdin, never on a command line), and disables the client again on exit. The
# token goes to a 0600 file as JC_K6_TOKEN_FILE and is deleted on exit.
#   JC_LOADTEST_CLIENT     the client, e.g. banskabystrica-loadtest (unset: anonymous run)
#   JC_KEYCLOAK_NAMESPACE  Keycloak's namespace, e.g. smartcity-keycloak
#   JC_KEYCLOAK_REALM      the realm, e.g. smartcity
#   JC_TOKEN_URL           the realm's token endpoint, https://idm.<domain>/realms/<realm>/protocol/openid-connect/token
#   JC_KEYCLOAK_POD        the pod kcadm runs in (default keycloak-app-keycloakx-0)
#   JC_KEYCLOAK_LOCAL_URL  Keycloak inside its pod (default http://localhost:8080)
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
client=${JC_LOADTEST_CLIENT:-}
token_file=""
# Switches the load generator's client on (true) or off (false) inside the Keycloak pod.
loadtest_client() {
  # shellcheck disable=SC2016 # the script runs in the pod and expands the pod's own variables
  kubectl -n "$JC_KEYCLOAK_NAMESPACE" exec "${JC_KEYCLOAK_POD:-keycloak-app-keycloakx-0}" -- bash -c '
    set -euo pipefail
    kc() { /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kcadm-loadtest.config; }
    kc config credentials --server "$0" --realm master \
      --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null
    id=$(kc get clients -r "$1" -q "clientId=$2" --fields id --format csv --noquotes)
    [ -n "$id" ] || { echo "no client $2 in realm $1" >&2; exit 1; }
    kc update "clients/$id" -r "$1" -s "enabled=$3"
    rm -f /tmp/kcadm-loadtest.config
  ' "${JC_KEYCLOAK_LOCAL_URL:-http://localhost:8080}" "$JC_KEYCLOAK_REALM" "$client" "$1"
}
# shellcheck disable=SC2317 # called from the EXIT trap
loadtest_off() {
  [ -z "$token_file" ] || rm -f "$token_file"
  [ -z "$client" ] && return 0
  loadtest_client false || echo "the client $client is still ENABLED: disable it in realm $JC_KEYCLOAK_REALM" >&2
}
receiver() {
  kubectl -n "$JC_PROM_NAMESPACE" patch prometheus "$name" --type merge -p "{\"spec\":{\"enableRemoteWriteReceiver\":$1}}" >/dev/null
  sleep 5
  kubectl -n "$JC_PROM_NAMESPACE" rollout status "statefulset/prometheus-$name" --timeout=300s >/dev/null
}
forward=""
watch=""
trap 'kill "$forward" 2>/dev/null || true; [ -z "$watch" ] || kill "$watch" 2>/dev/null || true; loadtest_off; receiver false || echo "the remote-write receiver is still ON: kubectl -n $JC_PROM_NAMESPACE patch prometheus $name --type merge -p {\"spec\":{\"enableRemoteWriteReceiver\":false}}" >&2' EXIT
if [ -n "$client" ]; then
  : "${JC_KEYCLOAK_NAMESPACE:?set JC_KEYCLOAK_NAMESPACE for JC_LOADTEST_CLIENT}"
  : "${JC_KEYCLOAK_REALM:?set JC_KEYCLOAK_REALM for JC_LOADTEST_CLIENT}"
  : "${JC_TOKEN_URL:?set JC_TOKEN_URL for JC_LOADTEST_CLIENT}"
  loadtest_client true
  token_file=$(mktemp)
  chmod 600 "$token_file"
  secret=$(kubectl -n "$JC_KEYCLOAK_NAMESPACE" get secret "keycloak-client-$client" -o jsonpath='{.data.client-secret}' | base64 -d)
  printf 'grant_type=client_credentials&client_id=%s&client_secret=%s' "$client" "$secret" |
    curl -fsS --data @- "$JC_TOKEN_URL" | jq -er .access_token > "$token_file"
  unset secret
  export JC_K6_TOKEN_FILE="$token_file"
fi
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
