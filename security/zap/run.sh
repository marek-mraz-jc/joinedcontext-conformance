#!/usr/bin/env bash
# OWASP ZAP baseline of the public dev hosts (T-5107): spider + passive rules only, no attack.
#
#   security/zap/run.sh [report dir]
#
# Scans the Portal, the catalogue, the API edge and the assistant one after another, then the
# Portal once more as the demo viewer when PORTAL_VIEWER_PASSWORD is set (PORTAL_VIEWER_USER
# defaults to demo.viewer@hel.fi). Each alert is judged by rules.tsv beside this file. Exit 0 when
# every scan has no unaccepted WARN or FAIL, 1 otherwise. Reports: <dir>/<target>.{json,md}.
#
# dev only, never production: the targets are fixed below and JC_ZAP_DOMAIN may name only dev.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
reports=$(mkdir -p "${1:-reports/zap}" && cd "${1:-reports/zap}" && pwd)
domain="${JC_ZAP_DOMAIN:-dev.joinedcontext.com}"
case "$domain" in
	dev.joinedcontext.com) ;;
	*) echo "refusing to scan $domain: the baseline runs against dev only" >&2; exit 2 ;;
esac
# zap-stable 2.17.0, the weekly build of 2026-10-08.
image="${JC_ZAP_IMAGE:-zaproxy/zap-stable@sha256:7aaa659b0d43078febd82e29bad112285c370727e86ab8340444220e17d9f0d2}"

targets=(
	"portal https://portal.$domain/"
	"catalogue https://opendata.$domain/"
	"api https://api.$domain/cs/ovzdusie/ngsi-ld/v1/types"
	"assistant https://ai.$domain/helsinki-public"
)

# ZAP writes its reports as the image's zap user.
chmod 0777 "$reports"
cp "$here/rules.tsv" "$reports/rules.tsv"

failed=()
scan() {
	local name="$1" url="$2"
	shift 2
	echo "== $name: $url"
	# -m 1: one minute of spidering, one thread: the edge's anonymous class allows 300 a minute.
	# --autooff: the Automation Framework path of 2.17 still counts an alert whose every instance
	# rules.tsv puts OUTOFSCOPE ("x 0"); the classic path drops it.
	# Exit 0 clean, 1 FAIL, 2 WARN, 3 ZAP error: every one but 0 marks the target.
	if docker run --rm -v "$reports:/zap/wrk:rw" "$@" "$image" \
		zap-baseline.py --autooff -t "$url" -c rules.tsv -m 1 -z "-config spider.thread=1" \
		-J "$name.json" -w "$name.md"; then
		return 0
	fi
	failed+=("$name")
}

for t in "${targets[@]}"; do
	scan "${t%% *}" "${t#* }"
done

if [ -n "${PORTAL_VIEWER_PASSWORD:-}" ]; then
	# The cookie lives in this process and the container's environment only; never printed.
	cookie=$(python3 "$here/edge_session.py" "https://portal.$domain/") || cookie=""
	if [ -n "$cookie" ]; then
		[ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::$cookie"
		ZAP_AUTH_HEADER_VALUE="$cookie" scan portal-viewer "https://portal.$domain/" \
			-e ZAP_AUTH_HEADER=Cookie -e ZAP_AUTH_HEADER_VALUE -e "ZAP_AUTH_HEADER_SITE=portal.$domain"
	else
		failed+=("portal-viewer (sign-in)")
	fi
else
	echo "== portal-viewer: skipped, PORTAL_VIEWER_PASSWORD is not set"
fi

if [ ${#failed[@]} -gt 0 ]; then
	echo "unaccepted alerts or errors in: ${failed[*]} (reports in $reports)" >&2
	exit 1
fi
echo "every baseline clean (reports in $reports)"
