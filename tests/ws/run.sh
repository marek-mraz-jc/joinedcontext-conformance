#!/usr/bin/env bash
# WebSocket notification channel suite (T-3518, Antares surfaces-conformance.md section 6).
# Scripted sessions whose every message is held against the broker's AsyncAPI document; extra
# arguments go to pytest. The document itself is validated by the caller with
# `npx @asyncapi/cli@2.16.0 validate` (the Antares job), not in the image.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${WS_URL:?set WS_URL to the view all channel, ws://.../x/views/all/ws}"
: "${ASYNCAPI_DOC:?set ASYNCAPI_DOC to docs/api/asyncapi-ws.yaml of the broker}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
