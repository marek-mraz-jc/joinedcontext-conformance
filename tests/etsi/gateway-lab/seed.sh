#!/usr/bin/env bash
# Writes the lab's views and rule document into the broker, as the Portal's reconciler would
# (portal src/reconciler/views.rs): the gateway serves every surface through the broker's view
# of the Endpoint (T-3643), and a view nobody wrote answers 404.
# Usage: seed.sh <broker base URL>
set -eu
broker=${1:?usage: seed.sh <broker base URL>}
dir=$(dirname "$0")/broker
for f in "$dir"/views/*.json; do
  curl -fsS -o /dev/null -X PUT -H 'content-type: application/json' --data-binary "@$f" \
    "$broker/q/views/$(basename "$f" .json)"
done
curl -fsS -o /dev/null -X PUT -H 'content-type: application/json' --data-binary "@$dir/policy.json" \
  "$broker/q/policy/bb-ovzdusie-ovzdusie"
