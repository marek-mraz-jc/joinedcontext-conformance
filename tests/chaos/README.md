# Cluster resilience under disruption (T-0088, CC-55, OPS-18)

`run.sh` holds a request rate against the gateway while it deletes one replica of the
context gateway, the portal and APISIX in turn and promotes a CNPG replica, and
`check_chaos_results.py` decides whether CC-55 held: at most 0.1 % failed requests, no gap
longer than 30 seconds, and every disruption answered again within 30 seconds.

The k6 exit code is not the verdict. k6 exits 0 on a run whose every request failed unless a
threshold happened to be declared, so the CSV stream of requests and the log of injected faults
are read by the checker, which is what CI and a human should look at.

## The owner's drills on `dev` (`dev-drills.sh`)

Admitted by the owner on 2026-10-18: dev only, never prod, one drill at a time, each announced in
`AI_shared_folder.md` (`tasks/note`) before it starts. The deployer runs it; a worker never does.

| Drill | How | Ready again means |
|---|---|---|
| kill one pod each of the gateway, the broker, the assistant and the Portal | `kubectl delete pod` of the first pod of each selector | a pod of the selector, not being deleted, is Ready |
| partition the broker from Postgres for 60 s | the NetworkPolicy `postgres-from-context-broker` is saved, deleted and applied again (also on exit) | the broker is Ready after the heal; the gateway's answers say whether it served |
| lose the Postgres primary | the primary pod is deleted: CNPG promotes a replica, or with one instance (dev) recreates the pod on its volume | a pod with `cnpg.io/instanceRole=primary` is Ready |

A NetworkPolicy only ever allows, so the partition takes the allow away rather than adding a deny;
a connection already open when it goes may survive, and the visitor's errors show whether one did.
Each drill is skipped, with the reason recorded, while a node is over 90 % memory
(`CHAOS_MAX_MEMORY`), a Helm release is pending or a rollout is under way, and the whole run holds
`CHAOS_LOCK` (`/workspace/.cache/dev-cluster.lock`) so the hourly apply can wait on the same lock.

`check_chaos_results.py --profile dev` is the verdict: every drill that ran is Ready again within
120 s (`--max-recovery`), the gateway answers again after each fault, and the broker's `entities`
and `attr_instances` hold at least as many rows after as before. The errors the k6 visitor (5
requests per second) saw during each drill are reported, not budgeted: one replica cannot keep
CC-55's 0.1 %.

```bash
CHAOS_I_MEAN_IT=yes KUBECONFIG=.secrets/kubeconfig-dev.yaml \
  GATEWAY_URL=https://api.dev.joinedcontext.com/api/endpoint/<slug>/ngsi-ld/v1 tests/chaos/dev-drills.sh
```

## CC-55 on a multi-replica cluster (`run.sh`)

This suite is **not executed** by qa. It writes to a cluster — it deletes pods and promotes a
database replica — and the only cluster that exists is the single-node `dev` box the deploy agent
owns and the demo runs on. Two things follow:

1. A single node cannot show what CC-55 asks. Deleting the one replica of a workload on a
   one-node cluster measures the restart time of that node's kubelet, not the disruption budget
   of a replicated service. The requirement needs at least two nodes and a PodDisruptionBudget.
2. Breaking the demo cluster to produce a green test is not a trade anyone asked for.

So `run.sh` refuses to start without `CHAOS_I_MEAN_IT=yes`, prints the kube context it is about
to disrupt, and injects one fault at a time with a recovery gap between them. Point it at a
staging cluster with more than one node when there is one; T-0007/T-0051 are the tasks that
create it.

## What is verified today

`python3 tests/chaos/check_chaos_results.py --selftest` runs the verdict over synthetic request
streams: a survivable pod deletion passes; a 40-second outage, a slow recovery, an error rate
above the budget, a surface that never answers again, a run too small to mean anything and a run
with no fault at all each fail with the reason named. That is the part of T-0088 that can be
proven without a cluster, and it is wired into the fast `ci` lane.

## Variables

| Variable | What |
|---|---|
| `GATEWAY_URL` | the NGSI-LD API root under load |
| `CHAOS_NAMESPACE`, `KUBECONFIG` | where the faults land, and the credentials for them |
| `CHAOS_I_MEAN_IT` | must be `yes`; without it the script exits 2 |
| `CHAOS_DURATION`, `CHAOS_RATE`, `CHAOS_GAP` | run length, request rate, seconds between faults |
| `CHAOS_GATEWAY_SELECTOR`, `CHAOS_PORTAL_SELECTOR`, `CHAOS_APISIX_SELECTOR` | label selectors of the workloads |
| `CHAOS_CNPG_CLUSTER`, `CHAOS_CNPG_REPLICA` | the CNPG cluster and the replica to promote |
| `JC_REPORTS_DIR` | where `requests.csv`, `disruptions.log` and `verdict.json` are written |
