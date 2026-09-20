# Platform end-to-end journey (T-0331: PL-01, EP-01, EP-61, SP-09, AP-01)

One set of buses followed from the feed that produced them to the catalogue that publishes
them, asserting at every hand-off that it is still the same set.

    HFP feed -> pipeline -> Vehicle entities in the `transport` space
             -> an Endpoint serving them as NGSI-LD and as GeoJSON
             -> an App declaring it needs exactly those representations
             -> a CKAN dataset whose resources are those representations
             -> a second space federating the first through a ContextSourceRegistration
             -> and a relationship linking every Vehicle back to what ingested it

> This is `tests/e2e`. The repository's `e2e/` at the root is the Playwright browser journeys
> and is a different suite with a different runner.

## What the suite proves

- **PL-01** — the pipeline left a *full* set. The HFP pipeline caps at thirty buses (T-0295),
  so fewer than thirty is a run that started and stopped, which looks identical to a healthy
  one in a message counter.
- **SP-09** — every id in the space carries this organization's and this space's prefix. An id
  minted under a foreign prefix inside a tenant surface is the tenancy failing, so it is
  checked on every entity rather than on a sample.
- **EP-01** — the Endpoint's two representations describe the same entities, and the Endpoint
  narrows its space rather than widening it. A GeoJSON carrying an entity the NGSI-LD
  representation does not is a second answer to the same question.
- **EP-61** — nothing the Endpoint's projection removes appears in either representation *or*
  in the JSON Schema. A schema that describes an attribute nobody is served still tells a
  reader the attribute exists.
- **AP-01** — the App that renders the last step declares `spec.kind`, `source`, `build`,
  `visibility`, `dataNeeds`, `limits` and a language-map title, and the representations it
  needs are the ones the catalogue carries.
- **T-0316 / T-0303** — every resource of the published dataset resolves (a `401` is the policy
  working, a `404` is a resource pointing at nothing), and the federated space answers with the
  registered space's ids, unchanged, because a registration federates rather than copies.
- **The graph is linked** — every entity points back at what ingested it. Without that link a
  Vehicle says nothing about where it came from and provenance stops at the pipeline's logs.

## Environment variables

Each names one stage. Without it that stage is reported as skipped, never as passed.

- `SPACE_URL`: the NGSI-LD base of the space the pipeline writes into, e.g.
  `https://<host>/cs/transport/ngsi-ld/v1`. `SPACE_TOKEN` is an optional bearer token for it.
- `ENDPOINT_URL`: an Endpoint serving that space, e.g. `https://<host>/api/endpoint/<slug>`.
  `GATEWAY_TOKEN` is an optional bearer token for it.
- `HIDDEN_ATTRIBUTES`: comma-separated attribute names the Endpoint's projection removes.
  Without it the two EP-61 checks skip, because a leak check that knows no secret proves
  nothing.
- `CKAN_URL`, `CKAN_DATASET`, `CKAN_API_TOKEN`: the catalogue and the dataset the publication
  left, read exactly the way `tests/ckan` reads them — that suite's client is imported rather
  than copied, so the two cannot drift.
- `FEDERATED_SPACE_URL`: a second space that federates the first through a
  ContextSourceRegistration.
- `APP_MANIFEST`: the `app.yaml` of the app that renders the journey. Without it the App rules
  are checked against `fixtures/app.yaml`.
- `JOURNEY_ORG`, `JOURNEY_SPACE`, `JOURNEY_TYPE`, `EXPECTED_VEHICLES`,
  `GRAPH_LINK_ATTRIBUTE`: the demo values (`hel.fi`, `transport`, `Vehicle`, `30`,
  `refDataSource`) unless an installation uses others.

## Fixtures

Three vehicles, not thirty. The committed fixtures carry the *shape* every entity, feature,
manifest, catalogue entry and registration has to have; the "thirty buses" claim is about a
running system and is asserted where `EXPECTED_VEHICLES` points at one. `selftest.py` serves
thirty and twenty-nine to prove the count rule itself.

## Run

```bash
SPACE_URL=https://<host>/cs/transport/ngsi-ld/v1 \
ENDPOINT_URL=https://<host>/api/endpoint/hsl-transport \
FEDERATED_SPACE_URL=https://<host>/cs/air-quality/ngsi-ld/v1 \
CKAN_URL=https://<host> CKAN_DATASET=hsl-transport \
HIDDEN_ATTRIBUTES=operatorNote \
jc-conformance e2e
```

## Self-test

Stands the whole journey up in one process and serves it broken one stage at a time — a short
run, a foreign prefix, a missing location, a GeoJSON short of one feature, a leak in the
entities and a leak in the schema, an Endpoint that widens, a dead resource, a federated space
answering fewer, and an entity with no ingest link:

```bash
python3 tests/e2e/selftest.py
```
