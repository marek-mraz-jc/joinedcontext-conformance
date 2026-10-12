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

## What a line means

- `fresh.<Type>`: the newest `modifiedAt`/`observedAt`/`dateObserved` against 1.5 times the
  writing Pipeline's period plus 10 minutes.
- `rep.<Type>.<format>`: the export's count against the NGSI-LD count, within 1 % (a live type
  has one entity of slack), the export within 10 s.
- `source.<Type>`: of 5 random entities, how many have their id or a value of five or more
  characters in the body of the source the DataSource names. FAIL below 3 of 5.
- `ws.notify`: a live type (written at least every 10 minutes) that changed during the window
  but sent no `x-notification` is a FAIL; one that did not change is a SKIP.

## Expectations per dataset

`expected/<dataset>.json` names the types a dataset must serve and how old each may be:

    {"types": {"BikeHireDockingStation": {"maxAge": "20m"}, "CityDistrict": {"maxAge": "8d"}}}

With it, `expected.types` fails when one of them is not served, and `fresh.<Type>` holds the type
to its `maxAge` (`<n>s|m|h|d`) instead of the seed's schedule; `"maxAge": null` only requires the
type, its freshness then comes from the seed (a type without a time attribute is a SKIP). One file per dataset, so the dataset tasks (T-4088..T-4099) never edit the same
file. The selftest reads every committed file, so a typo fails in CI.

`selftest.py` serves a recorded dataset from memory and breaks it one way at a time (empty value,
duplicate id, count mismatch, 403 export, stale type, WebSocket silence, wrong host, entity not
in the source); CI runs it with no network.
