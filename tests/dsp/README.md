# Dataspace Protocol conformance (DS-05, DS-08, DS-09)

The Eclipse Dataspace Protocol TCK (`eclipse-dataspacetck/dsp-tck` v1.0.2, DSP 2025-1) driven
against the connector addon in the provider and the consumer role: metadata endpoint, catalog,
contract negotiation and transfer process — `docs/Testing/02-conformance-tests.md` §6.

Task: T-0062. Requirements: DS-05 (pass the kit in CI for both roles), DS-08 (catalog), DS-09
(negotiation outcomes).

## Run

```bash
DSP_URL=https://host/api/v1/dsp \
DSP_BASE_URL=https://host/api/dsp \
DSP_PARTICIPANT_ID=did:web:banskabystrica.sk \
jc-conformance dsp
```

| Variable | Meaning |
|---|---|
| `DSP_URL` | the connector's dataspace protocol URL (`dataspacetck.dsp.connector.http.url`) |
| `DSP_BASE_URL` | the base URL that serves the metadata endpoint |
| `DSP_PARTICIPANT_ID` | the connector's agent id, `did:web:{orgDomain}` (DS-04) |
| `DSP_NEGOTIATION_URL`, `DSP_TRANSFER_URL` | where the TCK signals the connector to start a negotiation or a transfer in the consumer role; default `$DSP_BASE_URL/tck/{negotiations,transfers}/requests` |
| `DSP_AUTHORIZATION` | the authorization header the TCK attaches to every DSP request |
| `DSP_WAIT_MS` | how long the TCK waits for a connector response, default 10 000 |
| `DSP_SCENARIO_FILE` | dataset, offer and agreement ids for the scenarios, default `scenario.properties` |
| `DSP_TCK_IMAGE` | the TCK image, pinned by digest; override only to try a newer kit |

The connector must be reachable from the TCK container **and** must reach it back: the container
runs with `host.docker.internal:host-gateway`, so a connector on the host is addressed as
`host.docker.internal`.

## The exit code is not the verdict

The TCK runtime exits 0 even when everything failed. A real run against a connector that does
not answer at all ends with

```
Passed tests: 0
Failed tests: 65
...
Test run complete
```

and `$?` is still 0. `check_dsp_results.py` is therefore the gate: it fails on any `FAILED:`
outcome, on a log that never reached `Test run complete`, on a run that passed nothing, and on a
run that never exercised all three protocol areas (catalog, contract negotiation, transfer),
which DS-05 requires together. Failure reasons are reported with the test id that produced them.

## Self-test

```bash
python3 tests/dsp/check_dsp_results.py --selftest
```

The fixtures are excerpts of two real runs of the pinned image (the kit's own embedded connector,
and a run against a dead connector), so the parser is proven against the format the TCK really
writes, not a guessed one. It runs in the fast `ci` lane; the kit itself runs in the `dsp`
workflow, which needs a deployed connector.
