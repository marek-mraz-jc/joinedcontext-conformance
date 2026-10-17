#!/usr/bin/env bash
# Every distribution of every published dataset of the six publishers, paced (T-4386): the hourly
# batch's catalogue sweep. Reads CKAN_URL (`. tests/dev.env.sh catalogue-sweep`); writes the table
# to standards-sweep.md and one JUnit case per row to standards-sweep.xml; extra arguments go to
# standards_sweep.py. Exits 1 on any row that is neither OK nor RESTRICTED.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
cd "$here"
exec python3 standards_sweep.py --ckan "${CKAN_URL:?CKAN_URL names the catalogue}" \
	--out "$out/standards-sweep.md" --junit "$out/standards-sweep.xml" "$@" \
	bbsk banskabystrica zilina slovensko praha hel-fi
