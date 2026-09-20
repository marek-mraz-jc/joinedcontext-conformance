#!/usr/bin/env bash
# ETSI NGSI-LD Testing Task Force suite (T-0054, TS-05, TS-06).
#
# The upstream suite is fetched at a pinned commit instead of being vendored: it is ~10 MB of
# test purposes that upstream keeps moving, and a checkout is reproducible from the pin alone.
#   NGSILD_URL         NGSI-LD API root of the system under test (required)
#   TTF_LEGS           space separated suite folders under TP/NGSI-LD (default: the four below)
#   TTF_DIR            checkout location (default: $JC_REPORTS_DIR/../ngsi-ld-test-suite)
#   TTF_CALLBACK_HOST  address of THIS machine that the system under test can call back on
#                      (default 127.0.0.1; set it to the runner's routable address whenever the
#                      system under test is not in the same network namespace)
# Extra arguments are passed to robot.
set -euo pipefail

TTF_REPO=${TTF_REPO:-https://github.com/marek-mraz/ngsi-ld-test-suite.git}
TTF_REF=${TTF_REF:-5da9091e6b8e63690c6b09e0245b56ca4c4b9152}
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${JC_REPORTS_DIR:-$here/reports}
ttf=${TTF_DIR:-$(dirname "$out")/ngsi-ld-test-suite}
: "${NGSILD_URL:?set NGSILD_URL to the NGSI-LD API root of the system under test}"

# The suite hard-codes 172.28.0.18 for its three mock servers (resources/variables.py): the
# notification receiver, the Context Source mock and the @context server. That is the address the
# runner has inside the upstream project's compose network; anywhere else Robot tries to bind an
# address the machine does not have and STOPS THERE instead of failing. Each variable is both the
# bind address and the URL the system under test is told to call back on, so it has to be one
# address of this machine that the system under test can reach.
callback=${TTF_CALLBACK_HOST:-127.0.0.1}
python3 - "$callback" <<'BINDPROBE' || exit 2
import socket
import sys

host = sys.argv[1]
probe = socket.socket()
try:
    probe.bind((host, 0))
except OSError as exc:
    sys.exit(
        f"TTF_CALLBACK_HOST={host} is not an address this machine can bind ({exc}). "
        "The mock servers of the suite would hang instead of failing; set it to an address "
        "of this machine that the system under test can reach."
    )
finally:
    probe.close()
BINDPROBE

if [ ! -d "$ttf/.git" ]; then
  git clone --quiet --filter=blob:none "$TTF_REPO" "$ttf"
fi
git -C "$ttf" fetch --quiet origin "$TTF_REF"
git -C "$ttf" checkout --quiet "$TTF_REF"

# the suite's mock notification receiver is a vendored fork of robotframework-httpctrl; the
# published package is single-threaded and drops the callbacks the notification tests need.
python3 -c 'import httpctrl' 2>/dev/null || pip install --quiet --no-deps -e "$ttf/libraries/robotframework-httpctrl"

mkdir -p "$out"
legs=""
for leg in ${TTF_LEGS:-CommonBehaviours ContextInformation ContextSource DistributedOperations jsonldContext}; do
  [ -d "$ttf/TP/NGSI-LD/$leg" ] || { echo "no such leg: $leg" >&2; exit 2; }
  legs="$legs ./TP/NGSI-LD/$leg"
done

# the suites resolve resources/ and data/ relative to the working directory, as upstream's
# scripts/run_tests.sh does; running them from anywhere else fails every import.
cd "$ttf"

set +e
# shellcheck disable=SC2086  # $legs is a deliberate list of suite paths
robot \
  --outputdir "$out" \
  --variable "url:${NGSILD_URL}" \
  --variable "context_server_host:${callback}" \
  --variable "context_source_host:${callback}" \
  --variable "notification_server_host:${callback}" \
  --nostatusrc \
  "$@" $legs
robot_rc=$?
set -e
[ -f "$out/output.xml" ] || { echo "no report generated in $out" >&2; exit 1; }
[ "$robot_rc" -lt 250 ] || { echo "robot failed to run (rc=$robot_rc)" >&2; exit "$robot_rc"; }

exec python3 "$here/check_expected_failures.py" "$out/output.xml" "$here/expected_failures.json"
