# CKAN publication and DCAT-AP 3.0 conformance suite (T-0320: EP-27, EP-61…EP-67)

Validates that the DCAT-AP record an Endpoint answers with is DCAT-AP, that a publication
run left exactly one CKAN dataset behind it with resources that resolve and a DataStore
table that holds rows, and that nothing the Endpoint's policy set masks reached either.

## What the suite proves

- **EP-27 / EP-63**: the record at the Endpoint root is a `dcat:Dataset` carrying every
  mandatory DCAT-AP term. The CKAN dataset is built from that record and nothing else, so a
  record that conforms is a catalogue entry that conforms.
- **EP-68**: every distribution names an `dcat:accessURL` and a `dcat:mediaType`.
- **EP-69**: the record declares `dct:accessRights` as `PUBLIC` or `RESTRICTED`, which is
  what a harvester that never logs in reads to know whether the data behind it is open.
- **EP-62 / EP-64**: one dataset per declaring Endpoint, and every resource URL resolves.
  Resolving is the claim, not being open: a restricted Endpoint answering `401` is the
  policy working; a `404` is a resource pointing at nothing.
- **EP-65**: the DataStore table holds rows, each keyed by `entity_id`, and no entity is in
  it twice — which is what makes a refresh an upsert rather than an append.
- **EP-61 / EP-66**: an attribute the Endpoint masks appears in neither the record nor a
  DataStore row, as a value *or* as a column name. An empty column named after a masked
  attribute still tells a reader that the attribute exists.

## Environment variables

- `ENDPOINT_URL`: a running Endpoint. Without it the record checks run against the committed
  fixture only, which is what keeps the fast lane non-empty.
- `GATEWAY_TOKEN`: optional bearer token for that Endpoint.
- `CKAN_URL`, `CKAN_DATASET`: the catalogue and the dataset a publication run created.
  Without them every CKAN check skips.
- `CKAN_API_TOKEN`: optional, and only widens what CKAN answers — a public dataset needs
  none. The suite only ever reads: it creates, updates and deletes nothing, so pointing it
  at a production catalogue is safe.
- `MASKED_ATTRIBUTES`: comma-separated attribute names the Endpoint's policy hides. Without
  it the two leak checks skip, because a leak check that knows no secret proves nothing.
- `DCAT_AP_SHACL`: a `.ttl` file or a directory of them holding the official DCAT-AP 3.0
  SHACL shapes. Without it the records are validated against
  `fixtures/dcat-ap-3.0.core.shapes.ttl`, which is written in this repository and is **not**
  the SEMIC distribution: it is the mandatory core of `dcat:Dataset` and `dcat:Distribution`
  plus the `dct:accessRights` EP-69 adds. The official shapes, pinned by digest, are vendored
  under `dcat/shapes/` for the profile report below; point this variable at
  `dcat/shapes/dcat-ap/3.0.0/dcat-ap-SHACL.ttl` to use them here.

## National profiles (T-4510)

`profiles.py` holds each fixture record to DCAT-AP 3.0.0 (a gate: a violation fails), to
DCAT-AP-SK 3.0.0 and to DCAT-AP-CZ 2026-09-23 (reported until T-4499 writes records that meet
them), with the vendored shapes of `dcat/shapes/` (README there). A publisher's catalogue
(`fixtures/publisher-*.jsonld`, T-4586) is a gate on the profile it is rendered in, except the
upstream defects `UPSTREAM` names. `test_profiles.py` checks the digests, the gates, and that the
committed `evidence/dcat-profiles.md` is a fresh run; after a change to a record or a shape,
rewrite it:

    python3 tests/ckan/profiles.py --write evidence/dcat-profiles.md

The publisher fixtures are the gateway's own output: in joinedcontext-platform,
`DCAT_RECORDS_DIR=/tmp/r cargo test -p context-gateway --test publisher_catalog_tests` writes
them to `/tmp/r/publisher-*.jsonld`.

## Run

```bash
ENDPOINT_URL=https://data.example.org/api/endpoint/<slug> \
CKAN_URL=https://data.example.org CKAN_DATASET=kvalita-ovzdusia \
jc-conformance ckan
```

## Is every DataStore table complete, correct and current? (T-3112…T-3120)

`completeness.py` holds each DataStore table of a published dataset (one per entity type, T-3012)
to the Endpoint behind it: its rows equal the type's count (`count=true`), every attribute the
type's class declares in `model.schema.json` has a column, 20 sampled rows equal the broker's
entities attribute by attribute (a value the broker observed again since is counted as moved on,
not wrong), and its newest timestamp is within the age its pipeline's schedule allows; a table
with no time attribute is current while it equals the broker. Then every other resource answers
(200, or 401 for a restricted Endpoint) with content of its kind and a name of its own, and the
dataset page carries its title, description, licence, organisation, tags, publisher and frequency.

```bash
python3 tests/ckan/completeness.py https://data.dev.joinedcontext.com helsinki-bikes bbsk-registre \
  --max-age helsinki-bikes=1 --max-age bbsk-registre=336 --max-age banskabystrica-verejne:Event=48
```

prints a table per dataset and exits 1 on any FAIL. `CKAN_URL` and `CKAN_DATASETS` (comma-separated,
`CKAN_MAX_AGE_HOURS` for the age) run the same check under pytest; `test_completeness.py` holds the
check itself offline. `JC_GATEWAY_TOKEN` reads a restricted Endpoint.

## Does every Endpoint's dataset list every surface it serves? (T-3888, EP-108…EP-110)

`surfaces.py` reads every Endpoint of the projects named from the Portal and holds each to its
CKAN dataset: one resource per surface the Endpoint serves (its `enabledRepresentations`, or every
standard but `sta` less `disabledRepresentations`), none for a surface it does not, each with a
usage description and no credential in a URL or a text; an Endpoint without `spec.publish.ckan`
has no dataset and a restricted one is never public. Then each surface answers over its own URL:
`GET` 200 (401 restricted), a WebSocket upgrade whose `x-subscribe` answers 201, an MCP
`initialize`. With `--write`, one probe entity written through a writable Endpoint of the
conformance space measures the seconds until its DataStore row and the dataset's
`metadata_modified` change, as p50 and p95 over `--samples` writes. The hourly batch runs it
against dev (`PORTAL_TOKEN` from `tests/dev.env.sh`, read only; `JC_WRITER_TOKEN` only with
`--write`):

```bash
python3 tests/ckan/surfaces.py --portal https://portal.dev.joinedcontext.com \
  --ckan https://data.dev.joinedcontext.com helsinki banskabystrica
```

`test_surfaces.py` holds the comparison offline on a recorded `package_show`
(`fixtures/package-show-surfaces.json`): a missing resource, an extra one, an internal Endpoint
with a dataset, a credential in a URL or a usage text, each goes red.

## Self-test

Proves that the harness goes red on negative cases (a record with no access rights, a
distribution with no `accessURL`, a record that is not a Dataset, no dataset in CKAN at all,
a resource URL that 404s, an empty DataStore table, the same entity in it twice, and a masked
attribute in the record or in a row):

```bash
python3 tests/ckan/selftest.py
```

## A note on the network

The record's own `@context` is the remote `https://www.w3.org/ns/dcat.jsonld`. It is replaced
by a local prefix map before the graph is built (`dcat.py`): a conformance run that reaches
the open internet to parse its own input passes or fails on somebody else's uptime, and every
term the record uses is prefixed anyway.

## A project's catalogue, live (T-2790…T-2793, EP-78…EP-80)

`catalog.py` reads every endpoint of one project from the forge's bootstrap seed (`kubectl`,
read only; or `CATALOG_SLUGS=slug,slug` without a cluster), fetches each record anonymously as
JSON-LD and as Turtle, and checks that both are one graph, that they pass the pinned SEMIC
DCAT-AP 3.0.0 shapes (`DCAT_AP_SHACL`, else the platform clone's
`crates/context-gateway/tests/fixtures/dcat-ap/dcat-ap-3.0.0-SHACL.ttl`, refused unless its
sha256 is the pinned one), that the record carries publisher, contact desk, licence, themes,
spatial coverage, frequency and keywords in every language asked for, and that its ODRL offer
carries exactly the duties of its licence and constrains the recipient unless it is public.
It prints the table the task records and exits 1 when any endpoint fails. The hourly batch
runs it once per city:

```bash
python3 tests/ckan/catalog.py banskabystrica --languages sk,en
python3 tests/ckan/catalog.py bbsk --languages sk,en
python3 tests/ckan/catalog.py praha --languages cs,en
python3 tests/ckan/catalog.py helsinki --languages fi,sv,en
```

`test_catalog.py` proves each check can go red, on records the gateway wrote.
