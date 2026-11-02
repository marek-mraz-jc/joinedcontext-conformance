# Connector live checks

## claude.ai full-access connector (T-6024)

`claude_ai_checklist.yaml` holds 8 prompts, one per connector segment plus the refused approval,
each from a scenario the portal scripted lane passes (`tests/connector_scenarios/scenarios.yaml`).

1. Setup on dev (the deployer, never a worker without the cluster): person `t6024-full` and project
   `t6024-full` as the checklist's `setup` says; one connector of that person with every segment,
   writes on, allowed clients `https://claude.ai/oauth/mcp-oauth-client-metadata` only.
2. The owner adds the connector URL in claude.ai (Settings > Connectors), signs in as `t6024-full`
   and sends the prompts in order, one new chat each, at least a minute apart. The P2 change
   request is approved by `demo.approver` in the Portal.
3. Signed in to the Portal as `t6024-full`, save `GET /api/v1/connectors/{id}/activity` as
   `calls.json` and run `python3 tests/connectors/audit_table.py --calls calls.json`. The table
   goes into T-6024; exit 1 names each failing prompt.
4. Offboard the person and the project (T-5975).

`fixtures/claude_ai_calls.json` is built in the `ConnectorCall` shape of the Portal's OpenAPI; replace
it with the dev recording (scrubbed of nothing: the rows hold no token or input value) after step 3.

The deny half is `tests/mcp/test_connector_door.py::test_a_client_the_connector_does_not_allow_is_refused`
with `CONNECTOR_UNLISTED_CLIENT_TOKEN`: the same connector's token obtained by Claude Code is 403.

## Multi-step scenarios through Claude Code (T-6235 and the scenario wave)

`scenarios-wave/T-*.yaml` holds one scenario each: 3-8 prompts a steward types into ONE Claude Code
session, with a check per step (`tools` answered, `called`, `deny`, an `answer` regex, and
`refuse`: regexes over a call's arguments that the server must refuse, e.g. another project's name).

1. Sign the test person in to the full-access connector and write the bearer into a mode-600
   `cfg.json` (T-6025's evidence holds `signin.py`); give each run an evidence dir nobody else uses.
2. `python3 tests/connectors/claude_code.py wave --scenario scenarios-wave/T-6235.yaml
   --mcp-config cfg.json --out evidence/T-6235 --model claude-sonnet-5-5` starts one `claude -p` per
   step, `--resume`s the session for each later step and writes `<out>/step-NN.jsonl`.
3. `python3 tests/connectors/claude_code.py verdict evidence/T-6235 --scenario scenarios-wave/T-6235.yaml`
   prints the verdict table: `ok` where the step's own check holds, `check` where it does not. Judge
   each `check` row in `<out>/verdicts.yaml` (`step-03: {verdict: defect, proof: ...}`, `defect` or
   `model-error`, with the direct call that proves it); the same command prints the same table again.
4. `python3 tests/connectors/claude_code.py sizes evidence/T-6235 evidence/T-6240 ...` prints calls,
   refusals, answer bytes and USD per recorded run, then the 10 biggest answers (T-6252).

The connector allows 60 calls a minute across every session on it: scenario workers sharing one
connector get 429s when they run at the same time.

## All scenarios through Claude Code and the Portal assistant (T-5962)

`live-world.yaml` maps the catalogue's placeholders ("Endpoint X") to what the live project holds. Both
lanes read it, so they send the same words.

1. `python3 tests/connectors/claude_code.py run --world tests/connectors/live-world.yaml --mcp-config cfg.json
   --scenarios <portal>/tests/connector_scenarios/scenarios.yaml --project t6025-full --out DIR` as above.
2. `python3 tests/connectors/assistant_walk.py --out DIR --world tests/connectors/live-world.yaml
   --password-file FILE --user EMAIL --scenarios <portal>/tests/connector_scenarios/scenarios.yaml` signs the
   person in to the Portal and asks each scenario in a fresh conversation of the dock. `<id>.json` keeps the
   run's events, `<id>-1440.png` and `<id>-400.png` the Portal side. A question gets its first option, up
   to three times.
3. Write `DIR/verdicts.yaml`; `python3 tests/connectors/assistant_walk.py table DIR` prints the table.

Run one lane at a time: a change open for an object refuses the next change for it. The project's daily
assistant budget ended the 2026-11-02 walk after about 70 conversations.
