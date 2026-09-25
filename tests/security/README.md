# Security and policy tests (R1–R15, GW1–GW30, TS-25)

Adversarial checks of the enforcement point: PEP bypass and AST rewrite integrity, tenant
header stripping, 404-vs-403 silent narrowing, representation parity, AuthZEN decisions,
ODRL round-trip, prompt-injection corpus for the Agent Runner.

Tasks: T-0080…T-0088.

## Running

The suites talk to a deployed gateway; every variable they need names itself when it is missing.

| Variable | What |
|---|---|
| `PARITY_BASE_URL` | space or endpoint base URL for representation parity checks (e.g. `https://{host}/cs/ovzdusie` or `https://{host}/api/endpoint/{slug}`) |
| `PARITY_TYPE` | NGSI-LD entity type tested across representations (default: `AirQualityObserved`) |
| `PARITY_COLLECTION` | collection name for OGC Features path (default: lowercase `PARITY_TYPE`) |
| `PARITY_LIMIT` | maximum records to query during parity tests (default: `100`) |
| `GEO_ATTR` | geometry attribute name for spatial representations (default: `location`) |
| `STA_ID_FIELD` | identifier field for SensorThings observation parity mapping (default: `@iot.id`) |
| `SPACE_URL` | the space surface, e.g. `https://{host}/cs/ovzdusie/ngsi-ld/v1` |
| `OTHER_SPACE_URL` | a second space the caller has no grant on (SP-06 probe) |
| `TOKEN_VIEWER`, `TOKEN_STEWARD` | bearer tokens; anonymous is simply no token |
| `GRANTED_TYPE`, `FORBIDDEN_TYPE` | a type inside and a type outside the grant |
| `GRANTED_ENTITY_ID`, `FORBIDDEN_ENTITY_ID` | an entity the caller may read, and one that exists but is outside every grant |
| `HIDDEN_ATTR` | an attribute of `GRANTED_TYPE` the public grant does not include |
| `MCP_URL` | live MCP streamable HTTP surface for parameter sanitization checks (AG-21) |
| `MCP_TOOL` | MCP tool tested with hostile parameters (default: `query_entities`) |
| `AGENT_RUNNER_URL` | deployed Agent Runner API endpoint for TS-25 autonomous containment |
| `AGENT_RUNNER_TOKEN` | Bearer token for Agent Runner API session access |
| `AGENT_TASK` | task prompt used during live runner containment check |
| `AGENT_RUNNER_MANIFEST` | rendered Kubernetes manifest file or directory for sandbox isolation |
| `AGENT_RUNNER_PROFILE` | runner profile verified under AG-26 (`builder` default, or `steward`) |
| `INJECTION_CORPUS` | optional custom path to prompt injection YAML corpus (default: local corpus) |
| `AGENT_TRANSCRIPT` | a run's event frames as JSON (`GET /api/v1/projects/{p}/agent-runs/{id}/events` collected) checked for secret-shaped values and unlisted egress (T-0608, AG-35, AG-56) |
| `AGENT_ALLOWED_HOSTS` | comma-separated hosts the run profile allows (default: `registry.npmjs.org,portal.hel.fi`) |
| `AGENT_PROXY_URL`, `AGENT_PROXY_RUN`, `AGENT_PROXY_TICKET` | a live jc-agent-proxy and one run's ticket: an unlisted host is refused with the allow-list named (AG-50) and the diagnostics door answers without a secret (AG-56) |
| `PORTAL_URL`, `PROJECT_A`, `PROJECT_B` | the Portal's API root and two projects of two organisations, for the isolation suite (T-1708, PF-32) |
| `TOKEN_PROJECT_A`, `TOKEN_PROJECT_B` | one bearer token per project; each person is bound in their own project and in no other |
| `OTHER_ENDPOINT_URL` | an endpoint of project B, e.g. `https://{host}/api/endpoint/{slug}/ngsi-ld/v1` |
| `OTHER_ARTIFACT_URL`, `FORGE_URL`, `OTHER_FORGE_REPO`, `OTHER_RUN_ID` | one artifact, the forge root, `{owner}/{name}` and one agent run of project B |
| `BROKER_URL` | the broker's own address as seen from outside the cluster; the probe passes when nothing answers |
| `ACCESS_URL` | the effective grant surface, e.g. `https://{host}/cs/ovzdusie/access` or `/api/endpoint/{slug}/access` (EP-55, EP-56) |
| `ACCESS_CHECK_URL` | AuthZEN evaluation endpoint (default `ACCESS_URL` + `/check`, R51) |
| `FORGE_URL` | a **throwaway** forge under test, never dev (T-1703) |
| `FORGE_READER_TOKEN` | a token of a signed-in person in the read-only forge team; the identity the whole attack is played from |
| `FORGE_PLATFORM_TOKEN` | the Portal's own forge credential, played as one that has leaked; without it the two cases that need it skip |
| `FORGE_ORG`, `FORGE_REPO`, `FORGE_BRANCH` | where the configuration repository lives (defaults `joinedcontext`, `configuration`, `main`) |
| `FORGE_OTHER_ORG`, `FORGE_OTHER_REPO` | an organization of the same forge the reader is in no team of (default `mesto-kosice/configuration`) |
| `DATA_URL` | context broker NGSI-LD entities surface for access document parity checks (EP-55) |
| `PIPELINE_RUNNER_MANIFEST` | rendered pipeline-runner manifests (a file, a directory or a `kubectl get -o yaml` dump) judged for per-project isolation and default-deny egress; the fixtures beside the suites are the default (T-1701, T-1702) |
| `APP_NETWORKPOLICY` | the App pods' NetworkPolicies (a file, a directory or `kubectl get networkpolicy -n {release}-{project}-apps -o yaml`), judged for egress beyond DNS, the mesh, the gateway and the declared public networks (T-2839, AP-134, AP-135); the fixtures beside the suite are the default |

## Access surface & ODRL round-trip suites (T-0086, T-0087)

- **`test_authzen_access.py` (T-0086: R51, EP-55, EP-56, EP-59):** Validates that `GET …/access` answers
  the AuthZEN resource-search shape — `subject`, `resource`, `permissions[]`, `prohibitions[]`, one
  entry per grant, and the Endpoint's `limits` when it has any — residuals parse as CIM 009 query
  strings (`geoQ`, `temporalQ`, `scopeQ`, `q`), unauthorized types and hidden attributes are omitted
  without existence disclosure (R20), no entry grants a type and none of its attributes,
  `POST …/access/check` answers standard boolean AuthZEN decisions without policy leaks, and the
  document maintains parity with live data responses. Entries are folded per entity type before any
  comparison (`access_doc.by_type`), because two policies reaching one type are two entries.
- **`test_odrl_mapping.py` (T-0087: R26, R52, EP-57, MIM3-R10):** Validates that `Accept: application/odrl+json`
  and `Accept: text/turtle` negotiate conforming ODRL 2.2 policies in the `ngsi-ld:` profile, all leftOperands
  are defined by the profile, folded grants match the JSON access document without information loss
  (`q`, `scopeQ`, operations, attributes), and no forbidden types or hidden attributes leak across representations.

Both suites can be executed offline using the built-in self-test harness:

```bash
python3 agents/qa/joinedcontext-conformance/tests/security/selftest_access.py
```

## Pipeline runner suites (T-1701, T-1702)

- **`test_pipeline_escape.py` (T-1701: PL-18, PL-23, MF-39):** the runner's egress is default-deny —
  the Context Gateway, DNS and public addresses, and no rule that reaches a private range, a node
  or 169.254.169.254, because a check fetches a `DataSource` URL a person typed on that very
  runner. The manifest half of the vector (a mapping reading the environment or the runner's
  files, a `${VAR}` naming another source's secret, a processor the platform does not ship) is
  refused before anything runs and is proved in `jc-core`,
  `crates/jc-core/tests/pipeline_escape_tests.rs`.
- **`test_pipeline_project_isolation.py` (T-1702: PL-07):** two projects never share a runner
  process — a workload, a streams ConfigMap, a `pipeline-secrets` Secret and a files volume each,
  a CPU and memory limit per runner, and no API token, host namespace or writable root.

Both read rendered manifests rather than a cluster: `fixtures/pipeline-runner/conforming.yaml` has
to pass and `leaky.yaml` has to fail naming every defence it takes off, so a green run means the
analyser still bites. `PIPELINE_RUNNER_MANIFEST` points them at the real chart or a live dump.

The prompt-injection corpus integrity tests (`test_prompt_injection.py`) and the Agent Runner sandbox
manifest conformance checks (`test_agent_sandbox_isolation.py`) run entirely offline without requiring
any cluster deployment or live gateway. The live MCP leg (`MCP_URL`) and Agent Runner autonomous leg
(`AGENT_RUNNER_URL`) automatically skip by name whenever their respective environment variables are unset.

```bash
SPACE_URL=https://<host>/cs/ovzdusie/ngsi-ld/v1 jc-conformance security
```

The suites are read-only apart from the two denied writes they assert on, and those address ids
under the `conformance` URN prefix. `.github/workflows/security.yml` runs them on dispatch with
the tokens as repository secrets; the fast CI lane collects them so a broken fixture is caught on
every commit.

### Representation Parity (T-0085: TS-03, EP-06, EP-07)

The representation parity suite (`test_representation_parity.py`) queries the same context space or
endpoint base across all five standardized child representations (`ngsi-ld/v1/entities`, `file.csv`,
`file.geojson`, `ogc/features/collections/{collection}/items`, and `sta/v1.1/Observations`). It proves
that the Policy Enforcement Point (PEP) decision is evaluated identically across every representation:
entity sets match (EP-06, R20), normalized attribute projections and values agree (EP-07, TS-03), and
any forbidden or hidden attributes (`HIDDEN_ATTR`) are never leaked through any representation in either
parsed records or raw payloads.

### The forge as a side door to the configuration (T-1703: CC-41, PF-51)

Every change to the platform's configuration is a Change with a Verdict, and the forge is where that
Change ends up. `test_forge_side_door.py` plays the attack that skips the Verdict, as the least
privileged identity the platform has — somebody who signed in through Keycloak and landed in the
read-only forge team. One case per step: fork the configuration repository, create a repository to
copy it into, commit on the default branch, commit on the branch a Change is reviewed from, open a
pull request outside the Portal, read another organization's repository, list or add a repository
webhook, and spend the Portal's own forge credential as if it had leaked. Two further cases read the
branch protection rule itself: it pins the pusher to the Portal's identity, and it drops an approval
that a commit pushed after the Verdict has invalidated.

**This suite writes.** A defence that has failed leaves the repository changed, so it runs against a
throwaway forge and never against dev; `.github/workflows/forge-side-door.yml` refuses a URL on the
dev domain before it installs anything.

A suite of refusals passes beautifully against a forge that is not there, so it is not trusted until
it has been shown to go red. `selftest_forge.py` runs every case against `stub_forge_server.py` —
once hardened, where the whole suite is green, and once per planted door, where the case that watches
that door is named in the failures. That runs in the fast CI lane, needs no forge and no credential,
and is what makes a green live run mean something:

```bash
python3 tests/security/selftest_forge.py
```
## Proving a suite before it is run

`selftest_tenancy.py` runs `test_tenant_isolation.py` against `stub_tenancy_server.py`, a
two-project platform in one process: correct in `isolating` mode and wrong in one named way in
each of the others (`leaky_list`, `existence_disclosure`, `write_lands`, `artifact_served`,
`mcp_follows_argument`). It needs no deployed system, runs in the fast CI lane beside
`selftest_access.py`, `selftest_representations.py` and `selftest_forge.py`, and fails if the
suite passes against a platform that does not isolate.
