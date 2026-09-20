#!/usr/bin/env bash
# Golden input/output tests of the Bento transformation pipelines (T-0077, PL-22, PL-38).
# Discovers every test definition under ${PIPELINES_DIR} and runs `bento test` on it.
# Extra arguments go to `bento test`, e.g.  jc-conformance pipelines -- --log debug
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
mkdir -p "$out"

# Bento is deliberately not in the runner image: its binary carries HIGH and CRITICAL advisories
# from its own module pins (x/crypto ssh, grpc, thrift, amqp091), and an image we sign and publish
# does not get to carry those. Install it beside the suite instead, from the pinned upstream image.
command -v bento >/dev/null || {
  echo "bento is not on PATH — see tests/pipelines/README.md for the pinned image to take it from" >&2
  exit 2
}

dir=${PIPELINES_DIR:-/pipelines}
[ -d "$dir" ] || { echo "no pipelines to test: $dir does not exist (set PIPELINES_DIR)" >&2; exit 2; }

# `<config>_bento_test.yaml` is Bento's own convention; tests.yaml and test_definition.yaml are
# the names the platform docs use for the same thing.
mapfile -t definitions < <(find "$dir" -type f \
  \( -name '*_bento_test.yaml' -o -name 'tests.yaml' -o -name 'test_definition.yaml' \) | sort)

# a harness that passes because it found nothing is worse than no harness
[ ${#definitions[@]} -gt 0 ] || { echo "no golden test definition found under $dir" >&2; exit 2; }
printf 'golden test definitions under %s:\n' "$dir" | tee "$out/bento-test.log"
printf '  %s\n' "${definitions[@]}" | tee -a "$out/bento-test.log"

set +e
bento test "$@" "${definitions[@]}" 2>&1 | tee -a "$out/bento-test.log"
rc=${PIPESTATUS[0]}
set -e
exit "$rc"
