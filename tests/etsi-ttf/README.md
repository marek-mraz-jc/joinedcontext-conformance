# ETSI NGSI-LD Testing Task Force suite (TS-05, TS-06)

The official TTF Robot suite, fetched at a pinned commit (`TTF_REF` in `run.sh`) rather than
vendored, and run against `${NGSILD_URL}` across the four matrices of
`docs/Testing/02-conformance-tests.md` §1: vanilla broker, gateway embedded, gateway standalone,
and the context-space path `/cs/{space}/ngsi-ld/v1`.

All five legs of the suite are in the default `TTF_LEGS`, `DistributedOperations` included: it
drives the same single system under test with registrations that point at the suite's own Context
Source mock, so it needs no second broker — only a callback host the system under test can reach
(T-0267). Its first run against the in-memory broker leaves `5814_01_01` (a subscription is not
forwarded to the matching Context Source: `/csourceSubscriptions` answers `[]`) and `5814_01_02`
(`404` where `200` is expected) failing. Neither is triaged and neither is quarantined; a
distributed-operations failure is a claim about federation, and it gets made against a real
deployment or not at all.

```bash
NGSILD_URL=https://<host>/cs/<space>/ngsi-ld/v1 jc-conformance etsi-ttf
TTF_LEGS="ContextInformation" jc-conformance etsi-ttf -- --include mandatory
```

`expected_failures.json` is the quarantine list: `{suite, test_case, reason, expiration}`, where
`suite` and `test_case` are the names Robot prints — the file `5510_01.robot` is the suite
`5510 01`, and the test name is the full line, not the identifier alone.
`check_expected_failures.py` fails the run on an unexpected failure, on a quarantined test that
now passes, on an entry whose test no longer runs, and on an expired entry — the list cannot rot.
Its own logic is covered by `check_expected_failures.py --selftest`, which the fast CI lane runs.

The fast merge-request gate is the 31-case smoke suite in `../etsi/`; this one is the full run.

## What the system under test has to be

Three things decide whether a failure means anything, learned by running this against
`ghcr.io/marek-mraz/antares-broker:dev` on a laptop.

`TTF_CALLBACK_HOST` (default `127.0.0.1`) is the address of the machine running Robot that the
system under test can reach. The suite binds its notification receiver, its Context Source mock
and its `@context` server on it and hands the same address to the system under test as the URL to
call back. Upstream hard-codes `172.28.0.18` for all three, the address inside their compose
network; anywhere else Robot would try to bind an address the machine does not have and stop
there, so `run.sh` refuses an unbindable value before it starts (T-0267).

**A store that keeps temporal history.** The broker's in-memory store answers
`GET /temporal/entities/{id}` with the entity and no attribute instances, so every temporal test
purpose fails by construction — `020_*`, `021_*`, `049_*`, `016_01`, `017_01`, `5244_*`, `5611_*`,
`5731_*`, `5741_*`, `5744_*`. In a partial run against that configuration those were 153 of the
154 failing test purposes. None of them belongs in `expected_failures.json`; a temporal failure
is only evidence when the system under test persists temporal representations.

**One suite run per broker state.** `4233_01_01` was the one non-temporal failure, and it is a
state interaction, not a defect: earlier suites leave Context Source Registrations behind, and an
ordered query that could federate is `400 BadRequestData` by clause 5.7.2.4 ("ordering … not
limited to the local scope"). Measured on a fresh broker the same query is `200` and orders
`['A', 'b', 'á']`; with one registration matching `Vehicle` present it is `400` with that clause
in the detail. Quarantine it with that reason or run the leg against a clean broker.

## Gateway transparency (T-0056, TS-06, R15)

`transparency.sh` runs the same suite twice — at the broker and through the gateway under an
unrestricted administrative policy — and `compare_transparency.py` compares the two runs test by
test. A case that passes at the broker and fails through the gateway is an R15 divergence and
fails the build; a case that fails in both is the broker's own conformance gap, which the
quarantine list of `run.sh` already governs; a case that fails at the broker but passes through
the gateway is reported, because a gateway that answers what the broker does not is not
transparent either.

```bash
BROKER_URL=http://antares:8080/ngsi-ld/v1 \
GATEWAY_URL=https://host/cs/ovzdusie/ngsi-ld/v1 \
tests/etsi-ttf/transparency.sh

python3 tests/etsi-ttf/compare_transparency.py --selftest   # the comparison itself, in the ci lane
```
