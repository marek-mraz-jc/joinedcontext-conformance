# Published dataset validation (T-4087)

One read-only check that takes a CKAN dataset of the installation and validates its Endpoint
end to end: the catalogue entry, the NGSI-LD data type by type, freshness against the Pipeline
schedule, every representation's count (CSV, JSON, GeoJSON, XLSX, OGC, DataStore), the schema
artifacts, the WebSocket, OGC API Features, and a sample against the upstream source.

    python3 -m tests.datasets praha-mesto            # one dataset
    python3 -m tests.datasets --all                  # every dataset of the catalogue
    python3 -m tests.datasets --all --json > out.json

Each check prints one line, `PASS`, `FAIL`, `WARN` or `SKIP`, with the number it measured; the
exit code is 1 on any FAIL. WARN (a location outside the publisher's area) and SKIP (no time
attribute, no live type, a source that needs a key) say what was not held against the dataset.

## Environment

- `BASE_URL`: the installation, default `https://dev.joinedcontext.com`.
- `CKAN_URL`: its catalogue, default `https://data.<BASE_URL host>`.
- `SEED_DIR`: the deployment seed (`joinedcontext-deployment/components/context-gateway/seed`),
  where the Pipelines' periods and the DataSources' URLs are read; without it freshness and the
  source probe report SKIP.
- `JC_GATEWAY_TOKEN`: optional ServiceAccount token for a non-public Endpoint. It is sent only
  to the `BASE_URL` host, only after an anonymous 401/403, never across a redirect, and never
  printed or written.

## Expectations per dataset

`expected/<dataset>.json` holds what the dataset's validation task wrote down: `types`, every
type the dataset must serve (`expected.types` FAILs on one that is absent), and `max_age`, per
type, the oldest its newest write may be (`"2h"`, `"36h"`, `"11d"`), which `fresh.<Type>` holds
instead of the seed's schedule. One file per dataset, so the per-dataset tasks never edit the
same file; the selftest reads every file.

## What a line means

- `fresh.<Type>`: the newest `modifiedAt`/`observedAt`/`dateObserved` against 1.5 times the
  writing Pipeline's period plus 10 minutes.
- `rep.<Type>.<format>`: the export's count against the NGSI-LD count, within 1 % (a live type
  has one entity of slack), the export within 10 s.
- `source.<Type>`: of 5 random entities, how many have their id or a value of five or more
  characters in the body of the source the DataSource names. FAIL below 3 of 5.
- `ws.notify`: a live type (written at least every 10 minutes) that changed during the window
  but sent no `x-notification` is a FAIL; one that did not change is a SKIP.

`selftest.py` serves a recorded dataset from memory and breaks it one way at a time (empty value,
duplicate id, count mismatch, 403 export, stale type, WebSocket silence, wrong host, entity not
in the source); CI runs it with no network.
