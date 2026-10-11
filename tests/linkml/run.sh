#!/usr/bin/env bash
# LinkML surface suite of a view (Antares T-3512, surfaces-conformance.md section 5): the served
# schema judged by the pinned linkml (lint, gen-json-schema, gen-shacl) and pyshacl. Extra
# arguments go to pytest.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"
: "${LINKML_URL:?set LINKML_URL to the LinkML surface of view all, .../x/views/all/linkml}"
: "${LINKML_RESTRICTED_URL:?set LINKML_RESTRICTED_URL to the surface of view parity-restricted}"
exec python3 -m pytest --junitxml="$out/junit.xml" -q "$@" "$here"
