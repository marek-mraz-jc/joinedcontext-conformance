# Model Context Protocol conformance (SP-14)

Streamable HTTP transport, JSON-RPC 2.0 error and batching semantics, RFC 9728
`/.well-known/oauth-protected-resource`, RFC 8707 audience binding, tool annotation
integrity (`readOnlyHint`, `destructiveHint`) — `docs/Testing/02-conformance-tests.md` §4.

Tasks: T-0060 (protocol), T-0061 (data and configuration surfaces).
Requirements: SP-14…SP-20, EP-24, EP-25, AG-04…AG-13.

Three suites run here:

1. `test_mcp_protocol.py` (T-0060) — MCP Streamable HTTP transport framing, JSON-RPC 2.0
   framing, `initialize` protocol version negotiation, tool listing with JSON-Schema object
   definitions, tool annotations integrity (`readOnlyHint` on queries, `destructiveHint` on
   mutations, AG-07), session lifecycle (`Mcp-Session-Id` and `MCP-Protocol-Version`),
   RFC 9728 OAuth 2.1 protected resource metadata (SP-17), and notification semantics.
2. `test_mcp_isolation.py` (T-0061) — Context space tenant isolation (AG-05, SP-14: active
   space derived exclusively from URL path or token, never tool arguments), byte-identical
   cross-space error responses (SP-20), configuration plane non-mutating invariant (AG-06:
   proposals via Git MR/branch, no direct broker/DB write tools), and agent governance boundaries
   (AG-11: agent self-approval forbidden, approval lanes immutable; AG-13: restricted result
   grounding).
3. `test_mcp_read_parity.py` (T-1860) — the same read over the NGSI-LD route and over the
   tool, with one token: the ids, the attribute names and the values have to match, and so do
   the refusals. The request table is the read matrix of Architecture/07 §2, and
   `test_the_matrix_is_covered` reads the server's own `tools/list` to check that no tool and
   no argument of it is missing from the table — a new row without a parity case goes red.
   The REST door is derived from `ENDPOINT_MCP_URL`, so nothing new has to be configured for
   it; `MCP_NARROWED_TOKEN` proves the same table for a grant that sees less, and
   `MCP_PARITY_CASES=5` trims the table to the five-read smoke the hourly batch runs on dev.

## Run

```bash
MCP_URL=https://host/cs/ovzdusie/mcp jc-conformance mcp
```

| Variable | Meaning |
|---|---|
| `MCP_URL` | Per-space MCP surface under test (`https://host/cs/{space}/mcp`) |
| `ENDPOINT_MCP_URL` | Endpoint-specific MCP surface (`https://host/api/endpoint/{slug}/mcp`) |
| `CONFIG_MCP_URL` | Configuration MCP surface (`https://host/mcp/config` or `jcctl serve --mcp`) |
| `OTHER_SPACE_MCP_URL` | Second space MCP surface used for isolation and cross-space probe testing |
| `PRIVATE_MCP_URL` | Private space MCP requiring RFC 9728 OAuth 2.1 authentication |
| `MCP_TOKEN` | Bearer token for the authenticated caller (anonymous when unset, SP-17) |
| `MCP_AGENT_TOKEN` | Bearer token for an autonomous agent identity (AG-11 verification) |
| `PORTAL_MCP_URL` | The Portal MCP server over the operation registry, `https://portal.<domain>/api/v1/mcp` (T-0637; `test_portal_mcp.py` skips without it) |
| `PORTAL_MCP_PROJECT` | Project the bearer may draft in; the verdict-gate probe writes and drops one draft there |
| `CONFIG_MCP_SCRATCH` | Scratch resource identifier to exercise configuration proposal |
| `MCP_HIDDEN_ATTR` | An attribute the caller's policy masks, so AG-13 narrowing is observable |
| `OTHER_SPACE_TYPE` | A type the other space holds; the cross-space probes read it there, because a query tool takes a type |
| `CONFIG_MCP_TOKEN` | Bearer of the configuration plane when it is a different resource than the data plane (defaults to `MCP_TOKEN`) |
| `MCP_RESTRICTED_TYPE` | The entity type to ask for in that narrowed query (default `AirQualityObserved`) |
| `MCP_NARROWED_TOKEN` | Bearer of a grant that sees less than `MCP_TOKEN`'s, for the read parity suite (falls back to `MCP_TOKEN`) |
| `MCP_PARITY_CASES` | How many rows of the parity table to send; unset is the whole table, `5` is the dev smoke |

## Self-test

`selftest.py` spins up an in-process MCP server in conforming mode and in broken mode to prove
that the assertions pass valid implementations and catch every protocol framing or isolation
defect without needing a live platform deployment. It runs in the fast `ci` workflow lane.

```bash
python3 tests/mcp/selftest.py
```
