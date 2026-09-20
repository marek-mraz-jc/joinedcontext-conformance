#!/usr/bin/env bash
# Cluster resilience under single-replica disruption (T-0088, CC-55, OPS-18).
#
# Runs k6 against the gateway, injects one fault at a time while it runs, and hands the request
# stream and the fault log to check_chaos_results.py, which is the verdict — k6 exits 0 on a run
# whose every request failed.
#
# This script WRITES to a cluster: it deletes pods and promotes a database replica. It refuses to
# run without CHAOS_I_MEAN_IT=yes and it refuses to run against a cluster it was not pointed at
# explicitly, because a chaos run on the wrong context is an outage someone else has to explain.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

: "${GATEWAY_URL:?set GATEWAY_URL to the NGSI-LD API root of a space, e.g. https://host/cs/ovzdusie/ngsi-ld/v1}"
: "${CHAOS_NAMESPACE:?set CHAOS_NAMESPACE to the namespace holding the workloads}"
: "${KUBECONFIG:?set KUBECONFIG to a kubeconfig with delete rights in CHAOS_NAMESPACE}"

if [ "${CHAOS_I_MEAN_IT:-no}" != "yes" ]; then
  echo "refusing to disrupt $CHAOS_NAMESPACE: set CHAOS_I_MEAN_IT=yes when the cluster is yours to break" >&2
  exit 2
fi

command -v k6 >/dev/null || { echo "k6 is not on PATH (it is in the conformance runner image)" >&2; exit 2; }
command -v kubectl >/dev/null || { echo "kubectl is not on PATH" >&2; exit 2; }

context=$(kubectl config current-context)
echo "chaos run against context '$context', namespace '$CHAOS_NAMESPACE'" | tee "$out/chaos.log"

duration=${CHAOS_DURATION:-5m}
rate=${CHAOS_RATE:-50}
faults=$out/disruptions.log
: > "$faults"

JC_K6_RATE="$rate" JC_K6_DURATION="$duration" JC_REPORTS_DIR="$out" \
  k6 run --out "csv=$out/requests.csv" --quiet "$here/../k6/gateway-latency-load.js" \
  >>"$out/chaos.log" 2>&1 &
k6_pid=$!

# One fault at a time, each with time to recover inside the run: a simultaneous kill of everything
# measures nothing but the blast radius of the test.
inject() {
  local what=$1
  shift
  sleep "${CHAOS_GAP:-45}"
  printf '%s %s\n' "$(date +%s)" "$what" >> "$faults"
  echo "injecting: $what" | tee -a "$out/chaos.log"
  "$@" >>"$out/chaos.log" 2>&1 || echo "  (the fault command failed; the run continues)" | tee -a "$out/chaos.log"
}

delete_one_pod() {
  local selector=$1
  kubectl -n "$CHAOS_NAMESPACE" delete pod --wait=false \
    "$(kubectl -n "$CHAOS_NAMESPACE" get pod -l "$selector" -o name | head -1 | cut -d/ -f2)"
}

inject "delete one context-gateway pod" delete_one_pod "${CHAOS_GATEWAY_SELECTOR:-app=context-gateway}"
inject "delete one portal pod" delete_one_pod "${CHAOS_PORTAL_SELECTOR:-app=portal}"
inject "delete one APISIX pod" delete_one_pod "${CHAOS_APISIX_SELECTOR:-app.kubernetes.io/name=apisix}"
inject "CNPG switchover" kubectl -n "$CHAOS_NAMESPACE" cnpg promote \
  "${CHAOS_CNPG_CLUSTER:-jc-postgres}" "${CHAOS_CNPG_REPLICA:-jc-postgres-2}"

wait "$k6_pid" || echo "k6 exited non-zero; the verdict below is what counts" | tee -a "$out/chaos.log"

exec python3 "$here/check_chaos_results.py" "$out/requests.csv" "$faults" --json "$out/verdict.json"
