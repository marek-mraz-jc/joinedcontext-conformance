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
  plus the `dct:accessRights` EP-69 adds. The official shapes are not vendored here — they
  are a third-party distribution with its own licence and release cadence, and a conformance
  claim about them should be made against the file the operator actually points at.

## Run

```bash
ENDPOINT_URL=https://data.example.org/api/endpoint/<slug> \
CKAN_URL=https://data.example.org CKAN_DATASET=kvalita-ovzdusia \
jc-conformance ckan
```

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
