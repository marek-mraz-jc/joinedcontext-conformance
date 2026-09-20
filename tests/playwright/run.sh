#!/usr/bin/env bash
# Playwright browser journeys (T-0068, TS-12, TS-13). Extra arguments go to `playwright test`,
# e.g.  jc-conformance playwright -- journeys/01-onboarding-login.spec.ts --project=chromium
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
e2e=${JC_E2E_DIR:-$(cd -- "$here/../.." && pwd)/e2e}
[ -f "$e2e/playwright.config.ts" ] || { echo "no playwright config at $e2e" >&2; exit 2; }
export JC_REPORTS_DIR=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$JC_REPORTS_DIR"
exec playwright test --config "$e2e/playwright.config.ts" "$@"
