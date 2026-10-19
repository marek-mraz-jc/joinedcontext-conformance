#!/usr/bin/env bash
# The owner's drills on dev (T-0088, admitted 2026-10-18): one at a time, while k6 reads the
# gateway at a low rate, then check_chaos_results.py --profile dev is the verdict.
#
#   1. kill one pod each of the gateway, the broker, the assistant and the Portal;
#   2. partition the broker from Postgres for 60 s (its NetworkPolicy allow is taken away and put back);
#   3. lose the CNPG primary: a switchover where the cluster has a replica, the primary pod deleted
#      where it has none (dev runs one instance), which CNPG recreates on the same volume.
#
# Measured: how long each target took to be Ready again, the errors the k6 visitor saw, and the
# broker's row counts before and after (fewer after is data loss).
#
# This script WRITES to a cluster. Dev only, never prod: it refuses without CHAOS_I_MEAN_IT=yes,
# refuses a context whose name does not contain CHAOS_CONTEXT_MATCH (default `dev`), announces each
# drill in AI_shared_folder.md before it starts, and skips a drill while a node is over 90 % memory,
# a Helm release is pending or a rollout is under way. It holds CHAOS_LOCK for the whole run, the
# lock the hourly apply can take too, so the two never overlap.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

: "${GATEWAY_URL:?set GATEWAY_URL to the NGSI-LD API root of a space the drill reads}"
: "${KUBECONFIG:?set KUBECONFIG to the dev cluster}"
if [ "${CHAOS_I_MEAN_IT:-no}" != "yes" ]; then
  echo "refusing: set CHAOS_I_MEAN_IT=yes when the cluster is yours to break" >&2
  exit 2
fi
for tool in k6 kubectl helm flock python3; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 2; }
done

context=$(kubectl config current-context)
case "$context" in
  *"${CHAOS_CONTEXT_MATCH:-dev}"*) ;;
  *) echo "refusing: context '$context' is not a ${CHAOS_CONTEXT_MATCH:-dev} cluster" >&2; exit 2 ;;
esac

slug=${CHAOS_INSTANCE:-dev}
pg_ns=${CHAOS_PG_NAMESPACE:-$slug-postgres}
pg_cluster=${CHAOS_CNPG_CLUSTER:-postgres-cluster}
pg_db=${CHAOS_PG_DATABASE:-antares}
partition=${CHAOS_PARTITION_POLICY:-$pg_ns/postgres-from-context-broker}
partition_seconds=${CHAOS_PARTITION_SECONDS:-60}
ready_timeout=${CHAOS_READY_TIMEOUT:-300}
gap=${CHAOS_GAP:-30}
drills=$out/drills.jsonl
faults=$out/disruptions.log
: > "$drills"
: > "$faults"

exec 9>"${CHAOS_LOCK:-/workspace/.cache/dev-cluster.lock}"
flock -n 9 || { echo "refusing: another drill or an apply holds ${CHAOS_LOCK:-/workspace/.cache/dev-cluster.lock}" >&2; exit 2; }

announce() {
  local line="chaos drill on $context: $1 (T-0088)"
  echo "$line" | tee -a "$out/chaos.log"
  if [ -x "${CHAOS_ANNOUNCE:-/workspace/tasks/note}" ]; then
    "${CHAOS_ANNOUNCE:-/workspace/tasks/note}" "$line" >/dev/null 2>&1 || true
  fi
}

record() { # what started ended ready_after skipped
  python3 - "$@" >>"$drills" <<'PY'
import json, sys
what, started, ended, ready, skipped = sys.argv[1:6]
print(json.dumps({"what": what, "started": float(started), "ended": float(ended),
                  "ready_after_s": float(ready) if ready else None, "skipped": skipped or None}))
PY
}

# Why the cluster cannot take a drill now, or nothing.
busy() {
  local worst
  worst=$(kubectl top nodes --no-headers 2>/dev/null | awk '{gsub("%","",$5); if ($5+0 > m) m = $5+0} END {print m+0}')
  if [ "${worst:-0}" -gt "${CHAOS_MAX_MEMORY:-90}" ]; then echo "node memory at ${worst} %"; return; fi
  if [ -n "$(helm list -A --pending -q 2>/dev/null)" ]; then echo "a Helm release is pending (an apply is running)"; return; fi
  kubectl get deploy,statefulset -A -o json | python3 -c '
import json, sys
for item in json.load(sys.stdin)["items"]:
    status, meta = item.get("status", {}), item["metadata"]
    want = item.get("spec", {}).get("replicas", 1)
    if status.get("observedGeneration", 0) < meta.get("generation", 0) or status.get("updatedReplicas", want) < want:
        print(f"a rollout of {meta[\"namespace\"]}/{meta[\"name\"]} is under way"); break
'
}

counts() { # the broker's rows, as the superuser, which RLS does not narrow
  local primary
  primary=$(kubectl -n "$pg_ns" get cluster.postgresql.cnpg.io "$pg_cluster" -o jsonpath='{.status.currentPrimary}')
  kubectl -n "$pg_ns" exec "$primary" -c postgres -- psql -d "$pg_db" -tA -F' ' -c \
    "SELECT 'entities', count(*) FROM entities UNION ALL SELECT 'attr_instances', count(*) FROM attr_instances" \
    | python3 -c 'import json,sys; print(json.dumps({k: int(v) for k, v in (l.split() for l in sys.stdin if l.strip())}))'
}

# Seconds since `since` until a pod of `selector` in `ns` that is not being deleted is Ready, or
# nothing on a timeout. Not by name: a StatefulSet pod and a lone CNPG instance come back as the same.
ready_again() {
  local ns=$1 selector=$2 since=$3 deadline=$(( $(date +%s) + ready_timeout ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if kubectl -n "$ns" get pod -l "$selector" -o json | python3 -c '
import json, sys
ready = [p for p in json.load(sys.stdin)["items"] if not p["metadata"].get("deletionTimestamp")
         and any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))]
sys.exit(0 if ready else 1)'; then
      echo $(( $(date +%s) - since ))
      return
    fi
    sleep 1
  done
}

drill() { # what, then the command that injects it and prints the seconds to recovery
  local what=$1 why started ended ready
  shift
  sleep "$gap"
  why=$(busy || true)
  if [ -n "$why" ]; then
    echo "skipped: $what ($why)" | tee -a "$out/chaos.log"
    now=$(date +%s); record "$what" "$now" "$now" "" "$why"
    return
  fi
  announce "$what"
  started=$(date +%s)
  printf '%s %s\n' "$started" "$what" >>"$faults"
  ready=$("$@" 2>>"$out/chaos.log" || true)
  ended=$(date +%s)
  record "$what" "$started" "$ended" "$ready" ""
  echo "  $what: ready again after ${ready:-never} s" | tee -a "$out/chaos.log"
}

kill_one() { # namespace selector
  local pod since
  pod=$(kubectl -n "$1" get pod -l "$2" -o jsonpath='{.items[0].metadata.name}')
  since=$(date +%s)
  kubectl -n "$1" delete pod "$pod" --wait=false >>"$out/chaos.log"
  ready_again "$1" "$2" "$since"
}

restore_partition() {
  if [ -s "$out/partition.yaml" ]; then
    kubectl apply -f "$out/partition.yaml" >>"$out/chaos.log" 2>&1 && : > "$out/partition.yaml"
  fi
}
trap restore_partition EXIT

partition_broker() {
  local ns=${partition%%/*} name=${partition##*/} since
  kubectl -n "$ns" get networkpolicy "$name" -o json | python3 -c '
import json, sys
policy = json.load(sys.stdin)
for key in ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields"):
    policy["metadata"].pop(key, None)
policy.pop("status", None)
print(json.dumps(policy))' >"$out/partition.yaml"
  kubectl -n "$ns" delete networkpolicy "$name" >>"$out/chaos.log"
  sleep "$partition_seconds"
  since=$(date +%s)
  printf '%s %s\n' "$since" "heal the broker partition" >>"$faults"
  restore_partition
  ready_again "${CHAOS_BROKER_NAMESPACE:-$slug-context-broker}" "${CHAOS_BROKER_SELECTOR:-app.kubernetes.io/name=context-broker-broker}" "$since"
}

lose_primary() {
  local primary instances since
  primary=$(kubectl -n "$pg_ns" get cluster.postgresql.cnpg.io "$pg_cluster" -o jsonpath='{.status.currentPrimary}')
  instances=$(kubectl -n "$pg_ns" get cluster.postgresql.cnpg.io "$pg_cluster" -o jsonpath='{.spec.instances}')
  # With a replica CNPG promotes it; with one instance (dev) it recreates the pod on its volume.
  [ "${instances:-1}" -gt 1 ] || echo "  one instance: no replica to fail over to, the primary comes back on its volume" | tee -a "$out/chaos.log"
  since=$(date +%s)
  kubectl -n "$pg_ns" delete pod "$primary" --wait=false >>"$out/chaos.log"
  ready_again "$pg_ns" "cnpg.io/cluster=$pg_cluster,cnpg.io/instanceRole=primary" "$since"
}

echo "drills against context '$context'" | tee "$out/chaos.log"
counts >"$out/counts-before.json"

JC_K6_RATE="${CHAOS_RATE:-5}" JC_K6_DURATION="${CHAOS_DURATION:-12m}" JC_REPORTS_DIR="$out" \
  k6 run --out "csv=$out/requests.csv" --quiet "$here/../k6/gateway-latency-load.js" >>"$out/chaos.log" 2>&1 &
k6_pid=$!

drill "kill one context-gateway pod" kill_one "${CHAOS_GATEWAY_NAMESPACE:-$slug-context-gateway}" "${CHAOS_GATEWAY_SELECTOR:-app.kubernetes.io/name=context-gateway-gateway}"
drill "kill one context-broker pod" kill_one "${CHAOS_BROKER_NAMESPACE:-$slug-context-broker}" "${CHAOS_BROKER_SELECTOR:-app.kubernetes.io/name=context-broker-broker}"
drill "kill one assistant pod" kill_one "${CHAOS_ASSISTANT_NAMESPACE:-$slug-assistant}" "${CHAOS_ASSISTANT_SELECTOR:-app.kubernetes.io/name=assistant-worker}"
drill "kill one portal pod" kill_one "${CHAOS_PORTAL_NAMESPACE:-$slug-portal}" "${CHAOS_PORTAL_SELECTOR:-app.kubernetes.io/name=portal-portal}"
drill "partition the broker from postgres for ${partition_seconds}s" partition_broker
drill "lose the postgres primary" lose_primary

wait "$k6_pid" || echo "k6 exited non-zero; the verdict below is what counts" | tee -a "$out/chaos.log"
counts >"$out/counts-after.json"
announce "drills over, verdict in $out/verdict.json"

exec python3 "$here/check_chaos_results.py" "$out/requests.csv" "$faults" --profile dev \
  --drills "$drills" --counts "$out/counts-before.json" "$out/counts-after.json" --json "$out/verdict.json"
