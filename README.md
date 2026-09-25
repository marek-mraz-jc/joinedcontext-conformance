# joinedcontext-conformance

Every suite that judges a running installation rather than a tree of source: the standards
conformance runs, the security and MCP suites, the browser journeys, the load profiles. One
image, one entrypoint, one folder per suite, and a verdict a person can read.

## 1. What is in it

The suites:

- ETSI NGSI-LD robot suite (space surface and endpoints)
- OGC `ets-ogcapi-features10` (endpoint `ogc/features/`)
- SensorThings API v1.1 read-path tests
- MCP conformance (data and configuration surfaces)
- Dataspace Protocol test kit (connector addon, DS-05)
- schemathesis against the Portal API OpenAPI document
- k6 load profiles (thousands rps on endpoints, EP performance budgets)
- pySHACL validation of served examples against served SHACL (DM-46)

And the repository around them:

| Path | What |
|---|---|
| `Dockerfile` | the runner image: Python 3.12, Robot Framework, schemathesis, k6, Playwright with Chromium, pySHACL (T-0053) |
| `bin/jc-conformance` | entrypoint: `--list`, `--selftest`, or one or more suite names |
| `requirements.txt` | hash-pinned Python lock, generated with `pip-compile --generate-hashes` from `requirements.in` |
| `package.json`, `package-lock.json` | browser tooling of the image |
| `tests/<suite>/` | one folder per suite, each with a `README.md` and a `run.sh` the entrypoint calls |
| `scripts/` | checks that run on someone else's tree rather than against a deployment: `check-i18n.py` (TS-14), `generate-qualification-report.py` (T-0089), `compare-latency.py` (T-0090) |

## 2. How it fits

A suite here proves a claim the specification makes, against an installation that is actually
running: `docs/Testing/00-strategy.md` says which layer answers which question, and
`docs/Testing/02-conformance-tests.md` which standard each conformance suite is taken from
(TS-01…TS-25). The unit and integration tests of a component stay in that component's own
repository; what needs a deployment lives here.

## 3. Build

The runner image is the artifact: every suite runs inside it, in CI and on a laptop.

```bash
docker build -t conformance-runner:local .
```

## 4. Test

The suites need a deployment, but the harness around them does not, and these are the checks
the merge gate runs — that every suite is discoverable, that every Robot keyword resolves,
that every pytest suite imports, and that each verdict checker still fails a broken run:

```bash
pip install --require-hashes -r requirements.txt
JC_TESTS_DIR=tests bin/jc-conformance --list
robot --dryrun --outputdir /tmp/robot tests
python3 tests/etsi-ttf/check_expected_failures.py --selftest
python3 tests/mcp/selftest.py
python3 tests/e2e/selftest.py
```

`--selftest` on the entrypoint prints the version of every installed tool; the image lane runs
it on every build and refuses an image whose entrypoint does not run as `testrunner`.

## 5. Run a suite

```bash
docker run --rm \
  -e NGSILD_URL=https://<host>/cs/<space>/ngsi-ld/v1 \
  -v "$PWD/reports:/reports" \
  ghcr.io/marek-mraz-jc/joinedcontext-conformance:main etsi
```

Locally, without the image: `JC_TESTS_DIR=tests bin/jc-conformance etsi`.

### Updating the locks

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim-bookworm sh -c \
  'pip install pip-tools && pip-compile --generate-hashes --allow-unsafe --output-file=requirements.txt requirements.in'
npm install --package-lock-only
```

### Against `dev`

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

The schedules run the suites themselves and file what fails (T-2798):
`scripts/scheduled-dev.py hourly|6h|nightly --out <summary.json>`, then
`tasks/file-failures <summary.json>` on the board. `hourly` is the ETSI smoke wrapper, `6h` is
`ogc`, `sta`, `mcp` and `ckan`, `nightly` is `schemathesis`, `dsp` and `authz`. Each suite gets its profile,
writes JUnit into `reports/scheduled/<suite>/`, and each case becomes one result keyed
`<suite>/<case>`. A suite without a subject is not left out: it reports that it measured nothing,
which files one task until the subject exists.

The performance budgets run nightly through `scripts/budgets-dev.py --out <summary.json>` (T-2800):
`tests/k6/dev-budgets.js` on the endpoint surfaces, only while every node is under 85 % memory;
`e2e/budgets/pages.spec.ts` for the Portal pages, cold, as the demo viewer; and the assistant's
answer histogram from the Portal's `/metrics`. One night over a budget is recorded, two nights
in a row file a task, and `reports/budgets-history.json` keeps the trend.

| Suite | On `dev` |
|---|---|
| `etsi` | **`smoke.robot` only**, through the one endpoint bound to a writing service account: `scripts/etsi-smoke-dev.sh`. |
| `etsi-ttf` | **Never against `dev`** (owner's rule). The Testing Task Force suite runs in CI against a throwaway broker, where its fixtures may fail and its writes cost nothing. |
| `security` | Yes, against the `ovzdusie` space: the one surface here that narrows attribute by attribute, so a bypass suite can observe narrowing. |
| `mcp` | Yes. The data plane is the space and endpoint MCP of that space; the configuration plane is the Portal's operations registry, which takes a person's token (`CONFIG_MCP_TOKEN`). |
| `e2e` | Yes, the bus journey: the HFP pipeline, the transport endpoint and the catalogue entry it publishes. |
| `schemathesis` | Yes, against the Portal API and the gateway. It fuzzes a live instance, so run it when nobody is demoing. The nightly schedule sends GET and HEAD only: no throwaway project with an account bound to it alone exists on `dev`, and the fuzzer never writes into the demo projects. |
| `authz` | Yes, nightly: the Portal's authorization matrix (`tests/authz_matrix.yaml` of the Portal's clone) as the demo people, reads and the writes a row marks live (T-2797). |
| `playwright` (`e2e/`) | Yes, with `. tests/dev.env.sh portal`. The journeys sign in through the edge as the seeded demo people. |
| `ogc`, `sta` | `. tests/dev.env.sh ogc` (or `sta`) points at the first seeded Endpoint that enables `ogc-features` (or `sta`). None does today, so both report that they measured nothing. |
| `ckan` | `. tests/dev.env.sh ckan`; the catalogue is public on `dev` and needs no API token. |
| `dsp` | No subject: the dataspace connector addon is not deployed. |
| `k6`, `chaos` | Not on `dev` unless the owner asks. One node carries the demo; a load profile and a fault injection both take it away from whoever is watching. |
| `models`, `pipelines` | Not deployment suites: they check a tree of manifests, not a running instance. |

A failure that survives the profile is a platform finding, and becomes a task
(T-0780…T-0783 are the ones found this way).

## 6. Security

Report a vulnerability in the platform privately through the security advisories of the
repository it lives in — [platform](https://github.com/marek-mraz-jc/joinedcontext-platform/security/advisories/new)
for the gateway, the reconciler and the manifest model,
[portal](https://github.com/marek-mraz-jc/joinedcontext-portal/security/advisories/new) for the
management application. Please do not open a public issue for one.

A new dependency can bring a fresh advisory, so scan the image the way `image.yml` does
before pushing anything that changes it (`Dockerfile`, `requirements.in`, `package.json`):

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/.trivyignore:/.trivyignore" \
  aquasec/trivy image --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /.trivyignore \
  --exit-code 1 conformance-runner:local
```

A suite that finds a bypass is reporting a vulnerability, so it is written down the same way:
a case in `tests/security/`, the finding in the task, and never a screenshot of a token. No
credential belongs in this repository. A profile mints every bearer at run time from the
kubeconfig the shell already holds and exports it into the process; nothing is written to a
file, a report or a log, and `reports/` is the only thing a run leaves behind.

## 7. Working here

A new suite is a folder under `tests/` with its own `README.md` and `run.sh`, a row in the
table above, and a verdict checker with a `--selftest` that fails a broken run — a suite that
cannot go red proves nothing. Cite the requirement ids of the claim it checks, and name the
standard clause in the case itself rather than in a commit message.
