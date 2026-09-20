# SensorThings API v1.1 (TS-08)

The Sensing Profile read path of `sta/v1.1/` (T-0059): the entity sets the platform claims,
`$filter`, `$select`, `$expand`, `$top`/`$skip` pagination, 404 for entity sets outside the
profile and for unknown entities (EP-13), and 405 for every write (EP-13).

OGC publishes no CITE suite for SensorThings v1.1 — `ets-sta10` covers v1.0 — so, as
docs/Testing/02-conformance-tests.md §3 says, conformance is asserted by this integration suite
against the response bodies rather than by a TEAM Engine run.

## Run

```bash
STA_URL=https://host/api/endpoint/mobility/sta/v1.1 jc-conformance sta
```

| Variable | Meaning |
|---|---|
| `STA_URL` | STA root of the endpoint under test |
| `STA_TOKEN` | bearer token |

Every filter is derived from an observation that is really there and then asserted both ways —
the matching filter must return it and the negation must not, so a `$filter` the endpoint ignores
fails the suite instead of passing it.
