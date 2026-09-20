# joinedcontext-conformance

Conformance and system test suites run against a deployed instance (requirements TS-01…TS-25, `../docs/Testing/`):

- ETSI NGSI-LD robot suite (space surface and endpoints)
- OGC `ets-ogcapi-features10` (endpoint `ogc/features/`)
- SensorThings API v1.1 read-path tests
- MCP conformance (data and configuration surfaces)
- Dataspace Protocol test kit (connector addon, DS-05)
- schemathesis against the Portal API OpenAPI document
- k6 load profiles (thousands rps on endpoints, EP performance budgets)
- pySHACL validation of served examples against served SHACL (DM-46)

## Layout

| Path | What |
|---|---|
| `Dockerfile` | the runner image: Python 3.12, Robot Framework, schemathesis, k6, Playwright with Chromium, pySHACL (T-0053) |
| `bin/jc-conformance` | entrypoint: `--list`, `--selftest`, or one or more suite names |
| `requirements.txt` | hash-pinned Python lock, generated with `pip-compile --generate-hashes` from `requirements.in` |
| `package.json`, `package-lock.json` | browser tooling of the image |
| `tests/<suite>/` | one folder per suite, each with a `README.md` and a `run.sh` the entrypoint calls |
| `scripts/` | checks that run on someone else's tree rather than against a deployment: `check-i18n.py` (TS-14), `generate-qualification-report.py` (T-0089), `compare-latency.py` (T-0090) |

## Running a suite

```bash
docker run --rm \
  -e NGSILD_URL=https://<host>/cs/<space>/ngsi-ld/v1 \
  -v "$PWD/reports:/reports" \
  ghcr.io/marek-mraz-jc/joinedcontext-conformance:main etsi
```

Locally, without the image: `JC_TESTS_DIR=tests bin/jc-conformance etsi`.

`--selftest` prints the version of every installed tool; the image CI gate runs it on every
build and refuses an image whose entrypoint does not run as `testrunner`.

Before pushing anything that changes the image (Dockerfile, `requirements.in`, `package.json`),
scan it the way `image.yml` does — a new dependency can bring a fresh advisory:

```bash
docker build -t conformance-runner:local .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/.trivyignore:/.trivyignore" \
  aquasec/trivy image --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /.trivyignore \
  --exit-code 1 conformance-runner:local
```

## Updating the locks

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim-bookworm sh -c \
  'pip install pip-tools && pip-compile --generate-hashes --allow-unsafe --output-file=requirements.txt requirements.in'
npm install --package-lock-only
```

## Running against `dev`

`dev` is the one cluster and it is also the demo instance, so a run there is a run against
seeded data and live pipelines rather than against a fixture. `tests/dev.env.sh` holds what a
suite has to know about it — the endpoint slugs read from the forge's bootstrap seed, a small
granted type per surface, and the tokens minted from the cluster at run time:

```bash
export KUBECONFIG=.secrets/kubeconfig-dev.yaml
. tests/dev.env.sh security && JC_TESTS_DIR=tests bin/jc-conformance security
. tests/dev.env.sh etsi     && JC_TESTS_DIR=tests bin/jc-conformance etsi -- --include smoke
```

The profile takes the suite name because suites read the same variable for different subjects,
and it prints what it could not set and why. No secret is in it: every bearer comes from the
kubeconfig this shell already holds and is exported into the process, never written.

The hourly batch calls one wrapper for the ETSI lane, `scripts/etsi-smoke-dev.sh`: it sources the
profile, refuses to run without the writing token (31 cases failing on their setup read like a
broken platform), puts `~/.local/bin` on `PATH` for `robot`, and applies the gateway quarantine
list so the batch reads a verdict instead of a count.

| Suite | On `dev` |
|---|---|
| `etsi` | **`smoke.robot` only**, through the one endpoint bound to a writing service account: `scripts/etsi-smoke-dev.sh`. |
| `etsi-ttf` | **Never against `dev`** (owner's rule). The Testing Task Force suite runs in CI against a throwaway broker, where its fixtures may fail and its writes cost nothing. |
| `security` | Yes, against the `ovzdusie` space: the one surface here that narrows attribute by attribute, so a bypass suite can observe narrowing. |
| `mcp` | Yes. The data plane is the space and endpoint MCP of that space; the configuration plane is the Portal's operations registry, which takes a person's token (`CONFIG_MCP_TOKEN`). |
| `e2e` | Yes, the bus journey: the HFP pipeline, the transport endpoint and the catalogue entry it publishes. |
| `schemathesis` | Yes, against the Portal API and the gateway. It fuzzes a live instance, so run it when nobody is demoing. |
| `playwright` (`e2e/`) | Yes, with `. tests/dev.env.sh portal`. The journeys sign in through the edge as the seeded demo people. |
| `ogc`, `sta` | No subject: no Endpoint on `dev` enables the OGC Features or SensorThings representation, so both answer 404 there. |
| `ckan` | Read through the `e2e` profile; the catalogue is public on `dev` and needs no API token. |
| `dsp` | No subject: the dataspace connector addon is not deployed. |
| `k6`, `chaos` | Not on `dev` unless the owner asks. One node carries the demo; a load profile and a fault injection both take it away from whoever is watching. |
| `models`, `pipelines` | Not deployment suites: they check a tree of manifests, not a running instance. |

A failure that survives the profile is a platform finding, and becomes a task
(T-0780…T-0783 are the ones found this way).
