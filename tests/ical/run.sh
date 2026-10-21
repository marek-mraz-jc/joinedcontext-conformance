#!/usr/bin/env bash
# iCalendar RFC 5545 validator and the Event mapping of a view's feed (T-5044). The fixture cases run
# with nothing set; ICAL_URL (and ICAL_NGSILD_URL for the mapping) adds the live feed. Extra
# arguments go to pytest.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
