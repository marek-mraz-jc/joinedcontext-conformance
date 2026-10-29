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
