#!/usr/bin/env bash
# T-4544: a throwaway CKAN + Solr in docker, loaded with T-4497's 10,000 generated datasets, and
# package_search timed against it. Offline and local: never dev (the node runs at 91% memory).
# The images are the deployment chart's (components/ckan/images.yaml), pinned by digest.
#
#   tests/scale/ckan-rig.sh up | load | measure | down | all
#
# CKAN_RIG_PORT (default 42125) is where CKAN answers on 127.0.0.1. The sysadmin password and
# API token are made here, kept in a mode-600 file under the rig's temp dir, and never printed.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
port=${CKAN_RIG_PORT:-42125}
net=jc-scale-ckan
state=${CKAN_RIG_STATE:-${TMPDIR:-/tmp}/jc-scale-ckan}
url=http://127.0.0.1:$port

CKAN=ghcr.io/marek-mraz-jc/joinedcontext-ckan@sha256:38ce62c0341eed1fdcd571728eaca84db8faac4c8a394c5f3b6052fe99f987fd
SOLR=docker.io/ckan/ckan-solr@sha256:ef8e5d3e6be1678cfe50ceb14a8ebf16c36d44aac2de1086a285ce6807af64e6
REDIS=docker.io/library/redis@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
POSTGRES=docker.io/library/postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea

up() {
  mkdir -p "$state" && chmod 700 "$state"
  [[ -s $state/pg ]] || (umask 077 && head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' >"$state/pg")
  [[ -s $state/admin ]] || (umask 077 && head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' >"$state/admin")
  docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null
  docker run -d --name jc-scale-pg --network "$net" -e POSTGRES_USER=ckan -e POSTGRES_DB=ckan \
    -e POSTGRES_PASSWORD="$(cat "$state/pg")" "$POSTGRES" >/dev/null
  # CKAN's prerun gives up on a database that is not up yet and starts without it
  for _ in $(seq 60); do
    docker exec jc-scale-pg pg_isready -U ckan -d ckan >/dev/null 2>&1 && break
    sleep 1
  done
  docker run -d --name jc-scale-solr --network "$net" "$SOLR" >/dev/null
  docker run -d --name jc-scale-redis --network "$net" "$REDIS" >/dev/null
  # the image's ini loads the DataStore, which wants databases this search rig has no use for
  docker run -d --name jc-scale-ckan --network "$net" -p "127.0.0.1:$port:5000" \
    -e CKAN_SITE_URL="$url" -e CKAN__PLUGINS=envvars \
    -e CKAN_SQLALCHEMY_URL="postgresql://ckan:$(cat "$state/pg")@jc-scale-pg/ckan" \
    -e CKAN_SOLR_URL=http://jc-scale-solr:8983/solr/ckan -e CKAN_REDIS_URL=redis://jc-scale-redis:6379/0 \
    -e CKAN_SYSADMIN_NAME=rig -e CKAN_SYSADMIN_EMAIL=rig@example.org \
    -e CKAN_SYSADMIN_PASSWORD="$(cat "$state/admin")" --entrypoint sh "$CKAN" \
    -c 'ckan config-tool "$CKAN_INI" "ckan.plugins = envvars" && exec /srv/app/start_ckan.sh' >/dev/null
  for _ in $(seq 120); do
    curl -fsS "$url/api/3/action/status_show" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS "$url/api/3/action/status_show" >/dev/null || { docker logs --tail 40 jc-scale-ckan; exit 1; }
  (umask 077 && docker exec jc-scale-ckan ckan -c /srv/app/ckan.ini user token add rig rig \
    | tail -n 1 | tr -d '[:space:]' >"$state/token")
  [[ -s $state/token ]] || { echo "no API token was made" >&2; exit 1; }
  echo "CKAN up at $url"
}

load() { python3 "$here/ckan_search.py" load --url "$url" --token-file "$state/token"; }
measure() { python3 "$here/ckan_search.py" measure --url "$url"; }

down() {
  docker rm -f jc-scale-ckan jc-scale-redis jc-scale-solr jc-scale-pg >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -rf "$state"
}

case ${1:-} in
  up | load | measure | down) "$1" ;;
  all) up && load && measure ;;
  *) echo "usage: $0 up|load|measure|down|all" >&2; exit 2 ;;
esac
