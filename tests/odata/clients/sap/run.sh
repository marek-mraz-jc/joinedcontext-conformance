#!/usr/bin/env bash
# SAP Cloud SDK interop check (T-3510): generate the client from the view's $metadata, then query.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${ODATA_URL:?set ODATA_URL to the service root of view all}"
cd "$here"
npm ci --no-audit --no-fund
check() { # <service root> [hidden names, comma-separated]
  rm -rf edmx gen && mkdir edmx
  curl -sfS -H 'OData-MaxVersion: 4.0' "${1%/}/\$metadata" -o edmx/View.edmx
  npx generate-odata-client --input edmx --outputDir gen --transpile --overwrite
  node check.js "${1%/}/" "${2:-}"
}
check "$ODATA_URL"
# the restricted view hides NO2 (Antares ci/views/parity-restricted.json)
if [ -n "${ODATA_RESTRICTED_URL:-}" ]; then check "$ODATA_RESTRICTED_URL" NO2; fi
