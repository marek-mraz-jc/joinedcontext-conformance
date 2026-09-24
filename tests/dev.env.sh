#!/usr/bin/env bash
# Settings for a conformance run against the `dev` cluster (T-0784, TS-05, TS-09, TS-19).
#
#   . tests/dev.env.sh [security|mcp|e2e|etsi|schemathesis|portal]
#
# Source it, never run it: it exports the variables the suites read and it takes a suite name
# because two suites read the same name for different subjects (`SPACE_URL` is the narrowed
# space for the security suite and the pipeline's space for the journey suite).
#
# Nothing here is a secret. Every token is minted from the cluster at run time through the
# kubeconfig this shell already holds, is exported into the process and is written nowhere;
# every slug is read from the forge's bootstrap seed, because a slug typed into a file drifts
# from the seed the moment somebody reseeds.
#
# Without a kubeconfig the profile still sets every public URL and names what it could not
# mint, so an anonymous run works and reports the rest as skipped rather than as failed.

JC_DEV_BASE="${JC_DEV_BASE:-https://dev.joinedcontext.com}"
JC_DEV_IDM="${JC_DEV_IDM:-https://idm.dev.joinedcontext.com}"
JC_DEV_PORTAL="${JC_DEV_PORTAL:-https://portal.dev.joinedcontext.com}"
JC_DEV_CKAN="${JC_DEV_CKAN:-https://data.dev.joinedcontext.com}"
JC_DEV_NS="${JC_DEV_NS:-dev}"
JC_DEV_REALM="${JC_DEV_REALM:-dev}"
# Every entity id on this instance reads urn:ngsi-ld:{Type}:hel.fi:{space}:{localId}: the
# gateway is deployed with one organization domain and checks it on every write and every
# retrieve by id (GW20, SP-02), whichever project the endpoint belongs to.
JC_DEV_ORG="${JC_DEV_ORG:-hel.fi}"

_jc_suite="${1:-security}"
_jc_missing=""

# --- the cluster, read only ------------------------------------------------------------

# A seed key is the repository path with `/` as `__`; the dot of the file name is escaped for
# jsonpath. The same read `scripts/smoke.sh` does, for the same reason.
_jc_slug() {
	kubectl get configmap gitea-bootstrap-seed -n "$JC_DEV_NS" \
		-o "jsonpath={.data.$1\\.yaml}" 2>/dev/null | sed -n 's/^ *slug: *//p' | head -1
}

_jc_client_secret() {
	kubectl get secret "keycloak-client-$1" -n "$JC_DEV_NS" \
		-o jsonpath='{.data.client-secret}' 2>/dev/null | base64 -d 2>/dev/null
}

_jc_user_password() {
	kubectl get secret "keycloak-user-${1//./-}" -n "$JC_DEV_NS" \
		-o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null
}

# A client_credentials token of a workload. Prints the token or nothing; never the secret.
_jc_token_client() {
	local id="$1" secret
	secret=$(_jc_client_secret "$id") || return 0
	[ -n "$secret" ] || return 0
	curl -sS --max-time 20 -d grant_type=client_credentials -d "client_id=$id" \
		--data-urlencode "client_secret=$secret" \
		"$JC_DEV_IDM/realms/$JC_DEV_REALM/protocol/openid-connect/token" 2>/dev/null |
		sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}

# A person's token through the confidential client the edge uses. The realm stores the email
# as the username, so the login is `<user>@<org>`.
_jc_token_person() {
	local user="$1" client="apisix-gateway" secret password
	secret=$(_jc_client_secret "$client") || return 0
	password=$(_jc_user_password "$user") || return 0
	[ -n "$secret" ] && [ -n "$password" ] || return 0
	curl -sS --max-time 20 -d grant_type=password -d "client_id=$client" \
		--data-urlencode "client_secret=$secret" -d "username=${user}@${JC_DEV_ORG}" \
		--data-urlencode "password=$password" \
		"$JC_DEV_IDM/realms/$JC_DEV_REALM/protocol/openid-connect/token" 2>/dev/null |
		sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}

# The first id of a type actually served by a surface, so a fixture names an entity that is
# there this morning rather than one that was there when somebody wrote the file.
_jc_first_id() {
	curl -sS --max-time 25 -H 'Accept: application/json' "$1/entities?type=$2&limit=1" 2>/dev/null |
		sed -n 's/.*"id":"\([^"]*\)".*/\1/p' | head -1
}

_jc_note() { _jc_missing="${_jc_missing}  - $1
"; }

# --- the surfaces ----------------------------------------------------------------------

JC_DEV_SLUG_AIR=$(_jc_slug projects__banskabystrica__spaces__ovzdusie__endpoints__public-air)
JC_DEV_SLUG_TRANSPORT=$(_jc_slug projects__helsinki__spaces__helsinki__endpoints__helsinki-transport)
JC_DEV_SLUG_HUB=$(_jc_slug projects__helsinki__spaces__helsinki-hub__endpoints__helsinki-hub)
[ -n "$JC_DEV_SLUG_AIR" ] || _jc_note "the endpoint slugs (no kubeconfig for namespace $JC_DEV_NS): every endpoint URL is unset"

# The tokens the gateway accepts on this instance. A person's token carries `aud: portal-api`
# and the gateway refuses it on its own surfaces ("token is not bound to this resource"), so
# the reading identity here is a workload: the agent proxy's client, whose audience names every
# seeded endpoint and the gateway itself. The Portal takes the person's token and nothing else.
JC_DEV_TOKEN_GATEWAY=$(_jc_token_client helsinki-agent-proxy)
JC_DEV_TOKEN_WRITER=$(_jc_token_client banskabystrica-conformance)
JC_DEV_TOKEN_PERSON=$(_jc_token_person demo.steward)
[ -n "$JC_DEV_TOKEN_GATEWAY" ] || _jc_note "TOKEN_VIEWER / GATEWAY_TOKEN / MCP_TOKEN: no client_credentials token for helsinki-agent-proxy"
[ -n "$JC_DEV_TOKEN_WRITER" ] || _jc_note "NGSILD_TOKEN: no client_credentials token for banskabystrica-conformance, so every ETSI fixture write answers 403"
[ -n "$JC_DEV_TOKEN_PERSON" ] || _jc_note "CONFIG_MCP_TOKEN / PORTAL_TOKEN: no password grant for demo.steward"

case "$_jc_suite" in
security)
	# The `ovzdusie` space is the one surface on dev that narrows attribute by attribute: its
	# public grant lists five property names and leaves `reliability` and `refDevice` out, so a
	# bypass suite pointed at it can observe narrowing. The `helsinki` space grants whole types
	# to the public role and hides nothing, which is why the same suite proves nothing there.
	export SPACE_URL="$JC_DEV_BASE/cs/ovzdusie/ngsi-ld/v1"
	export GRANTED_TYPE="AirQualityObserved"
	export HIDDEN_ATTR="reliability"
	# A type that exists on this instance and no policy of this space grants.
	export FORBIDDEN_TYPE="Event"
	export TOKEN_VIEWER="$JC_DEV_TOKEN_GATEWAY"
	GRANTED_ENTITY_ID=$(_jc_first_id "$SPACE_URL" "$GRANTED_TYPE")
	if [ -n "$GRANTED_ENTITY_ID" ]; then
		export GRANTED_ENTITY_ID
	else
		_jc_note "GRANTED_ENTITY_ID: the space served no $GRANTED_TYPE"
	fi
	# FORBIDDEN_ENTITY_ID stays unset: `ovzdusie` holds one type and the public grant covers it,
	# so no entity there is both real and outside the grant, and an id from another space is
	# refused for its URN prefix (400) rather than narrowed to 404.
	_jc_note "FORBIDDEN_ENTITY_ID: no entity of this instance is real and outside the grant of its own space (the silent-narrowing cases skip)"
	# OTHER_SPACE_URL stays unset: SP-06 asks a space-bound token for another space, and the
	# only bearer the gateway accepts here is audience-wide, so the probe would test nothing.
	_jc_note "OTHER_SPACE_URL: dev mints no space-bound bearer, so the SP-06 mismatch probe has no subject (it runs in CI against the lab gateway)"
	if [ -n "$JC_DEV_SLUG_AIR" ]; then
		export ACCESS_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_AIR/access"
		export DATA_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_AIR/ngsi-ld/v1"
		# Thirteen entities across the representations the endpoint enables: NGSI-LD and GeoJSON
		# answer, CSV, OGC Features and SensorThings answer 404 because the endpoint does not
		# enable them, which is the "not served" the parity suite tolerates. A many-thousand-row
		# type would pass the file ceiling instead and report EP-06 as a 413.
		export PARITY_BASE_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_AIR"
		export PARITY_TYPE="AirQualityObserved"
		export PARITY_LIMIT="100"
		export MCP_URL="$JC_DEV_BASE/cs/ovzdusie/mcp"
		export MCP_TOOL="query_entities"
	fi
	# The Agent Runner cases need a live run: a run id, its ticket and its transcript are
	# produced by a journey, not by a profile.
	_jc_note "AGENT_RUNNER_URL, AGENT_PROXY_RUN, AGENT_TRANSCRIPT: a live agent run's ids (the TS-25 containment cases skip)"
	;;
mcp)
	# A per-space MCP surface that narrows, so AG-05 (the space comes from the path, never from
	# a tool argument) and AG-13 (a narrowed answer says so) both have a subject.
	export MCP_URL="$JC_DEV_BASE/cs/ovzdusie/mcp"
	export OTHER_SPACE_MCP_URL="$JC_DEV_BASE/cs/helsinki/mcp"
	export MCP_RESTRICTED_TYPE="AirQualityObserved"
	# The other space holds other types, and a query tool takes one: without this the
	# cross-space probes read nothing and prove nothing.
	export OTHER_SPACE_TYPE="NewsArticle"
	export MCP_HIDDEN_ATTR="reliability"
	export MCP_TOKEN="$JC_DEV_TOKEN_GATEWAY"
	[ -n "$JC_DEV_SLUG_AIR" ] && export ENDPOINT_MCP_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_AIR/mcp"
	# The hub's MCP surface (T-1209): one connector over two spaces. Public, so no token; the
	# member names are the registrations', which is what a `jc:source` has to match.
	if [ -n "$JC_DEV_SLUG_HUB" ]; then
		export HUB_MCP_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_HUB/mcp"
		export HUB_MEMBERS="indicators,transport"
		export HUB_TYPE="Vehicle"
	else
		_jc_note "HUB_MCP_URL: no helsinki-hub endpoint in the seed, so the hub cases skip"
	fi
	# The operations registry is the configuration plane (ADR-N-021): one MCP server, in the
	# Portal, reached with a person's token. It is also the private surface of SP-17 — it answers
	# 401 with a Bearer challenge naming its RFC 9728 metadata document.
	export CONFIG_MCP_URL="$JC_DEV_PORTAL/api/v1/mcp"
	export PORTAL_MCP_URL="$JC_DEV_PORTAL/api/v1/mcp"
	export PRIVATE_MCP_URL="$JC_DEV_PORTAL/api/v1/mcp"
	export PORTAL_MCP_PROJECT="helsinki"
	export CONFIG_MCP_TOKEN="$JC_DEV_TOKEN_PERSON"
	# MCP_AGENT_TOKEN stays unset: AG-11 asks what an autonomous agent may do on the
	# configuration plane, and dev mints no agent identity with the Portal's audience.
	_jc_note "MCP_AGENT_TOKEN: no agent identity on dev carries the Portal's audience (the AG-11 cases skip)"
	_jc_note "CONFIG_MCP_SCRATCH: a scratch resource the proposal case may write is not seeded"
	;;
e2e)
	# The journey follows the buses: the space the HFP pipeline writes into, the endpoint that
	# serves them and the catalogue entry that publishes them.
	export SPACE_URL="$JC_DEV_BASE/cs/helsinki/ngsi-ld/v1"
	export JOURNEY_ORG="$JC_DEV_ORG"
	export JOURNEY_SPACE="helsinki"
	export JOURNEY_TYPE="Vehicle"
	# The pipeline caps the fleet at thirty (PL-22) and the reaper drops a bus whose last fix is
	# stale, so a live instance holds up to thirty and fewer off-peak. Ten is the floor that
	# separates a feed that runs from one that stopped.
	export EXPECTED_VEHICLES="10"
	export GRAPH_LINK_ATTRIBUTE="refDataSource"
	export HIDDEN_ATTRIBUTES="contactPoint"
	export CKAN_URL="$JC_DEV_CKAN"
	export CKAN_DATASET="helsinki-transport"
	export GATEWAY_TOKEN="$JC_DEV_TOKEN_GATEWAY"
	[ -n "$JC_DEV_SLUG_TRANSPORT" ] && export ENDPOINT_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_TRANSPORT"
	export FEDERATED_SPACE_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_HUB/ngsi-ld/v1"
	_jc_note "CKAN_API_TOKEN: the catalogue is read anonymously here; a private dataset would need one"
	;;
etsi)
	# The smoke suite writes its own fixtures, so it runs through the one endpoint bound to a
	# service account that may write: `public-air` of the banskabystrica project, with the
	# conformance account's client_credentials token (GW22, PF-45).
	[ -n "$JC_DEV_SLUG_AIR" ] && export NGSILD_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_AIR/ngsi-ld/v1"
	export NGSILD_TOKEN="$JC_DEV_TOKEN_WRITER"
	export NGSILD_TENANT=""
	# The org-domain segment of the ids the suite mints is the instance's, not the project's:
	# the gateway is deployed with one `orgDomain` and refuses `banskabystrica.sk` here with 400.
	export NGSILD_ORG_DOMAIN="$JC_DEV_ORG"
	export NGSILD_SPACE="ovzdusie"
	export NGSILD_EXPECTED_FAILURES="tests/etsi/expected_failures_gateway.json"
	# The hub (T-1209): a space that holds only registrations, over the transport space and
	# the indicator space. The federated cases read it and skip when it is not there.
	if [ -n "$JC_DEV_SLUG_HUB" ]; then
		export NGSILD_FEDERATED_URL="$JC_DEV_BASE/api/endpoint/$JC_DEV_SLUG_HUB/ngsi-ld/v1"
	else
		_jc_note "NGSILD_FEDERATED_URL: no helsinki-hub endpoint in the seed, so the federated cases skip"
	fi
	;;
schemathesis)
	export PORTAL_URL="$JC_DEV_PORTAL"
	export PORTAL_TOKEN="$JC_DEV_TOKEN_PERSON"
	export GATEWAY_URL="$JC_DEV_BASE"
	export GATEWAY_TOKEN="$JC_DEV_TOKEN_GATEWAY"
	[ -n "$JC_DEV_SLUG_AIR" ] && export ENDPOINT_SLUG="$JC_DEV_SLUG_AIR"
	;;
portal)
	# The browser journeys. The Portal has a host of its own behind the edge login, and the
	# demo people are the ones the development profile seeds.
	export BASE_URL="$JC_DEV_PORTAL"
	export PORTAL_VIEWER_USER="demo.viewer@$JC_DEV_ORG"
	PORTAL_VIEWER_PASSWORD=$(_jc_user_password demo.viewer)
	if [ -n "$PORTAL_VIEWER_PASSWORD" ]; then
		export PORTAL_VIEWER_PASSWORD
	else
		_jc_note "PORTAL_VIEWER_PASSWORD: no seeded password for demo.viewer"
	fi
	export PORTAL_PROJECT="helsinki"
	export PORTAL_ENDPOINT="helsinki-all"
	export PORTAL_PIPELINE="hel-news"
	_jc_note "PORTAL_INVITE_URL, PORTAL_DRIFTED_FLOW, PORTAL_PUBLIC_DASHBOARD_URL: each names something produced out of band (those cases skip)"
	;;
*)
	echo "tests/dev.env.sh: unknown suite '$_jc_suite' (security|mcp|e2e|etsi|schemathesis|portal)" >&2
	;;
esac

echo "dev profile: $_jc_suite on ${JC_DEV_BASE#https://}"
[ -z "$_jc_missing" ] || printf 'not set on dev, and why:\n%s' "$_jc_missing"

unset _jc_suite _jc_missing
unset -f _jc_slug _jc_client_secret _jc_user_password _jc_token_client _jc_token_person _jc_first_id _jc_note
