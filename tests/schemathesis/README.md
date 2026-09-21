# API contract fuzzing (TS-09)

`schemathesis` against the OpenAPI documents published by the Portal API and by the Context
Gateway management surface. Any unhandled panic, 500, or schema divergence fails the run.

Tasks: T-0063 (Portal API), T-0064 (gateway schema and access endpoints).

## Run

```bash
PORTAL_URL=https://portal.throwaway.example jc-conformance schemathesis   # never dev
```

| Variable | Meaning |
|---|---|
| `PORTAL_URL` | Portal API root; the OpenAPI document is read from `$PORTAL_URL/api/v1/openapi.json` (UI-05) |
| `PORTAL_OPENAPI_URL` | override when the Portal OpenAPI document is published elsewhere |
| `PORTAL_TOKEN` | Portal bearer token; without it operations answer 401 and fuzzing stays shallow |
| `PORTAL_TOKEN_VIEWER`, `PORTAL_TOKEN_STEWARD`, `PORTAL_TOKEN_APPROVER` | one run per role whose token is set, each with its own `junit-portal-<role>.xml` (the operations that role reached); a viewer run fails on any accepted write (T-1658). Set, they replace the single `PORTAL_TOKEN` run |
| `GATEWAY_URL` | Context Gateway root for `/api/endpoint/{slug}/...` endpoint tests |
| `GATEWAY_OPENAPI_URL` | Context Gateway management OpenAPI document URL (if published) |
| `ENDPOINT_SLUG` | an endpoint slug the caller has grants to read |
| `GATEWAY_TOKEN` | optional bearer token for the Context Gateway |
| `SCHEMA_MAJOR` | schema major version for endpoint artifact tests (default: `v1`) |
| `JC_SCHEMATHESIS_EXAMPLES` | test cases per operation, default 500 (the floor TS-09 sets) |
| `JC_SCHEMATHESIS_SEED` | fix the seed to replay a failing run |

Arguments after `--` go to `schemathesis run`, e.g. `-- --include-path-regex projects`.

## Gateway Endpoint Surface (T-0064)

The endpoint artifact surface (`/api/endpoint/{slug}/schema/…`, `/api/endpoint/{slug}/access`) has no OpenAPI document, so pytest executes invariant assertions directly: content negotiation (EP-49), strong ETags and caching (EP-48), schema artifacts (EP-46), AuthZEN / ODRL / grant-ast shapes (EP-55..EP-58), hostile query parameters (TS-09), and slug validation with R20 silent narrowing and existence disclosure prevention.

## Checks

Built-in `--checks all` validates every response against the published document. On top of that
`jc_checks.py` validates it against the contract in `docs/API/01-portal-api.md`, which the
document itself does not express:

- **`ui05_error_is_problem_json`** — every 4xx/5xx is `application/problem+json` with `type`,
  `title` and a `status` equal to the HTTP status, and the type URI under
  `https://joinedcontext.com/errors/`.
- **`pf50_viewer_never_writes`** — in a run as the viewer (`JC_SCHEMATHESIS_ROLE=viewer`, set by
  `run.sh` for the viewer's token), no write answers 2xx: the viewer holds no write verb (PF-50).
- **`ts09_no_internal_detail_leak`** — no response body carries a Rust panic or backtrace, a
  database DSN, an SQL statement, a server-side source path or a bearer token.

## Self-test

`selftest.py` runs the real CLI twice against a stub Portal and twice against a stub Gateway in-process: conforming stubs must pass, while broken stubs (panicking on error, ignoring content negotiation, or lacking strong ETags) must fail naming the relevant checks. It needs no network and runs in the fast `ci` lane, so checks that can no longer go red are caught immediately.

```bash
python3 tests/schemathesis/selftest.py
```
