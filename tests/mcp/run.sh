#!/usr/bin/env bash
# Model Context Protocol (MCP) conformance suite (T-0060, T-0061, SP-14…SP-20, AG-04…AG-13).
# Extra arguments go to pytest.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${MCP_URL:?set MCP_URL to the MCP endpoint under test, .../cs/{space}/mcp}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
