#!/usr/bin/env bash
# Microsoft.OData.Client interop check (T-3510): view all, then the restricted view without NO2.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${ODATA_URL:?set ODATA_URL to the service root of view all}"
dotnet run --project "$here" -- "$ODATA_URL"
# the restricted view hides NO2 (Antares ci/views/parity-restricted.json)
if [ -n "${ODATA_RESTRICTED_URL:-}" ]; then dotnet run --project "$here" -- "$ODATA_RESTRICTED_URL" NO2; fi
