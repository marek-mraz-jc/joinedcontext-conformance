#!/usr/bin/env bash
# The one ETSI suite that runs against the dev cluster: `smoke.robot`, 31 cases (T-0930, TS-12).
# The Testing Task Force suite never touches dev — it runs in CI against a throwaway broker.
#
#   scripts/etsi-smoke-dev.sh [robot arguments…]
#
# Reads its settings from `tests/dev.env.sh`, which mints the conformance account's token from
# the cluster at run time; nothing is written to the repository, the report or a log line.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$here"

: "${KUBECONFIG:?point KUBECONFIG at the dev cluster (.secrets/kubeconfig-dev.yaml)}"

# shellcheck source=tests/dev.env.sh
. tests/dev.env.sh etsi

[ -n "${NGSILD_URL:-}" ] || { echo "no endpoint seeded on the dev gateway: nothing to run against" >&2; exit 1; }
# A run without the write grant fails all 31 cases in the setup with 403, which reads like a
# broken platform. Say what is missing instead.
[ -n "${NGSILD_TOKEN:-}" ] || { echo "no token for the conformance service account: the suite would fail every case on its setup" >&2; exit 1; }

# `pip install --user` puts robot here, and the image's PATH does not carry it.
PATH="$HOME/.local/bin:$PATH"
export PATH
command -v robot >/dev/null || {
	echo "robot is not installed: python3 -m pip install --break-system-packages robotframework==7.4.2 robotframework-requests==0.9.7 robotframework-jsonlibrary==0.5" >&2
	exit 1
}

export JC_REPORTS_DIR="${JC_REPORTS_DIR:-$here/reports/etsi-smoke-dev}"
exec tests/etsi/run.sh --include smoke "$@"
