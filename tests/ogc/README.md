# OGC API - Features conformance (TS-07)

Two things run here (T-0057, T-0058):

1. `test_cql2_filtering.py` — the query contract the platform owns: the exact set of conformance
   classes it claims (EP-30), `bbox`, `datetime`, CQL2 comparison and its rejection of
   unsupported constructs (EP-34, EP-35), pagination (EP-36), 404 on an unknown feature (EP-33)
   and 405 on every write (EP-39). Filters are derived from a feature that is really there, then
   asserted to exclude it and to include it again — a filter that returns everything fails.
2. the official `ets-ogcapi-features10` Abstract Test Suite, driven through TEAM Engine, with
   `check_ogc_results.py` turning its TestNG document into a verdict (TS-07, EP-40).

## Run

```bash
OGC_LANDING_URL=https://host/api/endpoint/mobility/ogc/features jc-conformance ogc
```

| Variable | Meaning |
|---|---|
| `OGC_LANDING_URL` | landing page of the endpoint under test |
| `OGC_COLLECTION` | collection to exercise (default: the first one advertised) |
| `OGC_TOKEN` | bearer token |
| `OGC_COLLECTIONS` | how many collections the ATS covers (default 3, `-1` for all) |
| `TE_BASE_URL` | an already running TEAM Engine; without it `run.sh` starts the pinned image with docker |
| `OGC_SKIP_ATS=1` | run only the pytest suite, e.g. where docker is not available |

The TEAM Engine image is pinned by digest in `run.sh`. It is not part of the conformance runner
image: it is a 1 GB Tomcat application that only the OGC lane needs, and it runs as its own
container.

## Self-test

`check_ogc_results.py --selftest` proves the verdict can go red: a failed assertion, a report
without the Core conformance class, an empty report and a report holding only TestNG
configuration methods all fail. It runs in the fast `ci` lane. The parser was written against a
real 161-test TEAM Engine run, not against a guess at the format.
