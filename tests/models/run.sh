#!/usr/bin/env bash
# LinkML artifact conformance test runner (T-0078, TS-18, DM-02, DM-03, DM-43, DM-46).
# Verifies that every kind: DataModel in PROJECTS_DIR has valid committed artifacts,
# valid draft-07 JSON Schema, matching semantic versions, and passing pySHACL checks.
set -euo pipefail

here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

# Model Tools is deliberately not in the runner image (the generators live in the platform
# repository, tools/model-tools/). The regeneration test skips cleanly unless MODEL_TOOLS_CMD
# is provided.
dir=${PROJECTS_DIR:-}
if [ -z "$dir" ] || [ ! -d "$dir" ]; then
  echo "PROJECTS_DIR is unset or not a directory: '$dir' (set PROJECTS_DIR to the projects tree)" >&2
  exit 2
fi

set +e
python3 -m pytest "$here" -q -p no:cacheprovider --junitxml="$out/models.xml" "$@" 2>&1 | tee "$out/models.log"
rc=${PIPESTATUS[0]}
set -e
exit "$rc"
