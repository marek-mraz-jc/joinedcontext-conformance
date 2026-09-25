# Authorization matrix, live (T-2797)

The Portal's `tests/authz_matrix.yaml` says, for every route, what each role of the taxonomy gets.
The Portal's own test holds every cell on every push against a mocked forge; this suite sends
the cells dev can answer, as the demo people, nightly (`scripts/scheduled-dev.py nightly`).

```bash
. tests/dev.env.sh authz && tests/authz/run.sh
```

| Variable | Meaning |
|---|---|
| `PORTAL_URL` | the Portal, e.g. `https://portal.dev.joinedcontext.com` |
| `JC_AUTHZ_MATRIX` | the table; default the Portal's clone beside this repository |
| `AUTHZ_TOKEN_<ROLE>` | a bearer token of a person of that role (`VIEWER`, `EDITOR`, `STEWARD`, `APPROVER`, `ORG_ADMIN`, `OTHER_MEMBER`, `SERVICE_ACCOUNT`); a role without one is skipped |
| `AUTHZ_PROJECT` | the project the cells name, default `helsinki` |

Only reads, and the writes a row marks `live: true`: a resource proposal with `?dryRun=All`, or
a call on an object that does not exist. A refusal must answer its status exactly; an `allow`
cell must not answer 401 or 403.
