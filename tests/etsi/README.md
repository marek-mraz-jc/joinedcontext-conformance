# ETSI NGSI-LD conformance (TS-05, TS-06)

Robot Framework suites from the ETSI NGSI-LD Testing Task Force, run against the four
system-under-test matrices of `docs/Testing/02-conformance-tests.md` §1: vanilla broker,
gateway embedded, gateway standalone, endpoint slug path.

- `smoke.robot` — merge-request lane, the clauses the demo depends on (T-0055).
- `temporal.robot` — temporal read suite (CIM 009 clauses 6.18–6.20, EP-30).
- `temporal_projected.robot` — temporal reads through a ModelProjection (EP-30, MP-02).
- `temporal_federated.robot` — federated temporal reads over registrations (EP-70).
- the full Testing Task Force suite and its quarantine list live next door in `../etsi-ttf/`.

Run: `NGSILD_URL=https://<host>/cs/<space>/ngsi-ld/v1 jc-conformance etsi`

Against `dev` the run is the `etsi-smoke` workflow dispatch: its defaults point at the seeded
endpoint of the dev gateway, mint a token for the conformance service account from the
repository secret `NGSILD_CLIENT_SECRET` (T-0277), and apply the gateway quarantine list.

`tests/etsi/run.sh` reads five variables from the environment:

| Variable | Default | What it is |
|---|---|---|
| `NGSILD_URL` | none, required | the NGSI-LD API root of the system under test |
| `NGSILD_TENANT` | empty | the `NGSILD-Tenant` every request carries |
| `NGSILD_TOKEN` | empty | the bearer token, for a surface that needs one |
| `NGSILD_ORG_DOMAIN` | `conformance` | the org-domain segment of the ids the suite mints |
| `NGSILD_SPACE` | `smoke` | the space segment of the same ids |
| `NGSILD_PROJECTED_URL` | empty | projected endpoint API root (skips projected suite when empty) |
| `NGSILD_FEDERATED_URL` | empty | federated endpoint API root (skips federated suite when empty) |
| `NGSILD_EXPECTED_FAILURES` | none | a quarantine list in the `etsi-ttf` format; robot's verdict is replaced by `check_expected_failures.py` |

The last two matter the moment the system under test is a Context Gateway rather than a broker: an
entity id must read `urn:ngsi-ld:{Type}:{orgDomain}:{space}:{localId}` for the organization and the
context space of the endpoint being written through (SP-02, GW20), and an id from anywhere else is
refused with `400` at the write guard. Set them to the deployment's own values and every fixture
create is a `201`; leave them at their defaults against a gateway and all 31 cases fail on the
suite setup rather than on their clause (T-0274).

```bash
. tests/dev.env.sh etsi && jc-conformance etsi -- --include smoke
```

`tests/dev.env.sh` sets those five for `dev` from the cluster itself: the writing endpoint's
slug from the forge seed, the conformance account's token, `NGSILD_SPACE=ovzdusie` and the
instance's own `NGSILD_ORG_DOMAIN=hel.fi` — the gateway is deployed with one organization
domain and refuses an id under the project's own `banskabystrica.sk` with a `400`. The
local lab further down keeps its own project and space names: it is a self-contained fixture
with a broker of its own, not a copy of any deployment, so its values are read from the
manifests in `gateway-lab/` and not from here.

## Running it against a broker on your own machine

A deployment serves `/cs/{space}` and `dev` has one, but the suite is also verified against the
default broker directly, with no gateway in front.
This is how the two `5.7.2.4` cases were found (T-0265); the runner image is `linux/amd64` only,
so on an arm64 machine robot comes from a plain python image instead.

```bash
docker run -d --name jc-antares -p 9090:9090 \
  ghcr.io/marek-mraz/antares-broker@sha256:c049084076e389d2cb85be2b22235a9f64b810cd4a71b63bddf9a460b7f762b9
printf 'FROM python:3.12-slim\nRUN pip install --no-cache-dir robotframework==7.4.2 robotframework-requests==0.9.7\n' \
  | docker build -q -t jc-robot:local -
docker run --rm --network host -v "$PWD/tests:/tests:ro" -v "$PWD/reports:/reports" jc-robot:local \
  robot --outputdir /reports --variable NGSILD_URL:http://localhost:9090/ngsi-ld/v1 \
  --variable TENANT: --variable TOKEN: /tests/etsi
```

31 tests, 31 passed is the current state against that image. The broker runs with an in-memory
store and an allow-all policy engine, so a green run says the suite agrees with the broker on
CIM 009 and nothing at all about the gateway's policy layer.

## Running it through a Context Gateway on your own machine

The gateway is the `/api/endpoint/{slug}` matrix of `docs/Testing/02-conformance-tests.md` §1, and
it answers differently from a bare broker: it checks the entity id against the endpoint's
organization and space, it rewrites the query it forwards, and it raises its own errors. Give it a
manifest repository — `gateway-lab/` here is the smallest one that works: a `ContextSpace`, a
public `Endpoint`, and a `Policy` granting `redirectionOps` and the batch operations to the role
`public` — and point the suite at the endpoint's slug.

```bash
docker network create jclab
docker run -d --name jc-broker --network jclab -e ANTARES_STORE=memory \
  ghcr.io/marek-mraz/antares-broker@sha256:c049084076e389d2cb85be2b22235a9f64b810cd4a71b63bddf9a460b7f762b9
docker run -d --name jc-gw --network jclab --platform linux/amd64 \
  -v "$PWD/tests/etsi/gateway-lab:/repo:ro" \
  -e JC_GATEWAY_BROKER_URL=http://jc-broker:9090 \
  -e JC_GATEWAY_ORG_DOMAIN=banskabystrica.sk -e JC_GATEWAY_REPO_DIR=/repo \
  ghcr.io/marek-mraz-jc/joinedcontext-platform:main
docker run --rm --network jclab -v "$PWD:/work" -w /work \
  -e NGSILD_URL="http://jc-gw:8080/api/endpoint/adldef3aksndswos6ug4pv5cpf/ngsi-ld/v1" \
  -e NGSILD_ORG_DOMAIN=banskabystrica.sk -e NGSILD_SPACE=ovzdusie \
  jc-robot:local tests/etsi/run.sh
```

The gateway image is `linux/amd64` only as well. `JC_OIDC_ISSUER` is left unset, so the gateway
serves only the endpoints whose audience is `public` and every request is the anonymous caller,
which holds the role `public` and nothing else (GW22).

`.github/workflows/etsi-gateway.yml` runs exactly this on every push to `main` and nightly, with
`NGSILD_EXPECTED_FAILURES=tests/etsi/expected_failures_gateway.json` (T-0276): a failure outside
the list goes red, and so does a listed case that starts passing, so the list has to shrink when
the fix lands.

The list is empty. It held three entries — T-0271 (`georel=near;maxDistance==N` forwarded as a
`maxDistance` parameter), T-0272 (`application/problem+json` and non-ETSI error types, against
CIM 009 clause 5.5.3) and T-0273 (415 answered as 400), each reproduced against the broker for
contrast before it was filed. Platform `0368014` fixed all three, so 31 of 31 is the state
through that image and the entries were removed (T-0364).
