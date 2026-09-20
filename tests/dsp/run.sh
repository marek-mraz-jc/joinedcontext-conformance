#!/usr/bin/env bash
# Eclipse Dataspace Protocol TCK against the connector addon (T-0062, DS-05).
# Renders the TCK configuration from the environment, runs the pinned TCK image, and lets
# check_dsp_results.py decide: the runtime exits 0 even when every test failed.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

: "${DSP_URL:?set DSP_URL to the connector dataspace protocol URL, e.g. https://host/api/v1/dsp}"
: "${DSP_BASE_URL:?set DSP_BASE_URL to the connector base URL that serves the metadata endpoint}"
: "${DSP_PARTICIPANT_ID:?set DSP_PARTICIPANT_ID to the connector agent id, did:web:{orgDomain} per DS-04}"

image=${DSP_TCK_IMAGE:-eclipsedataspacetck/dsp-tck-runtime@sha256:f5f7fcc80f607dad680f363ba32f9abf6762aa9c4f54a2a211f6fec2582730fd}

config="$out/config.properties"
{
  printf 'dataspacetck.debug=true\n'
  printf 'dataspacetck.dsp.local.connector=false\n'
  printf 'dataspacetck.dsp.connector.agent.id=%s\n' "$DSP_PARTICIPANT_ID"
  printf 'dataspacetck.dsp.connector.http.url=%s\n' "$DSP_URL"
  printf 'dataspacetck.dsp.connector.http.base.url=%s\n' "$DSP_BASE_URL"
  printf 'dataspacetck.dsp.connector.negotiation.initiate.url=%s\n' "${DSP_NEGOTIATION_URL:-$DSP_BASE_URL/tck/negotiations/requests}"
  printf 'dataspacetck.dsp.connector.transfer.initiate.url=%s\n' "${DSP_TRANSFER_URL:-$DSP_BASE_URL/tck/transfers/requests}"
  printf 'dataspacetck.dsp.default.wait=%s\n' "${DSP_WAIT_MS:-10000}"
  # the connector answers the TCK with the caller's own credential material, never a shared key
  if [ -n "${DSP_AUTHORIZATION:-}" ]; then
    printf 'dataspacetck.dsp.connector.http.headers.authorization=%s\n' "$DSP_AUTHORIZATION"
  fi
  # dataset, offer and agreement ids the connector must serve for the scenario tests
  scenario=${DSP_SCENARIO_FILE:-$here/scenario.properties}
  if [ -f "$scenario" ]; then
    cat "$scenario"
  fi
} > "$config"

# the TCK reaches the connector over HTTP and the connector calls back into the TCK
docker run --rm --name "dsp-tck-$$" \
  --add-host "host.docker.internal:host-gateway" \
  --mount "type=bind,source=$config,target=/etc/tck/config.properties,readonly" \
  "$image" 2>&1 | tee "$out/tck.log"

exec python3 "$here/check_dsp_results.py" "$out/tck.log"
