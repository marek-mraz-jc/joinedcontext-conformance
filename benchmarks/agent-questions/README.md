# Agent benchmark of the open-data connector (T-4496)

Twenty-five questions a person asks the dev MCP hub (`questions.json`). Each one carries the NGSI-LD
read its expected answer comes from, fetched right before the question runs, and the tolerance
an answer is matched with. `agent_bench.py run` asks each through `claude -p` on the
subscription, with the hub as the only MCP server and no built-in tool, and writes a table of
correct answers, tool calls and tokens per question, and, for the five questions that name a parameter (`uses`: join, aggrMethods, georel, lang, T-4533; datasetId, T-4557), whether a tool call passed it. The scoring has its tests in
`tests/test_agent_bench.py`.

```bash
python3 benchmarks/agent-questions/agent_bench.py expected        # the expected values, no model call
python3 benchmarks/agent-questions/agent_bench.py run --out /tmp/before
python3 benchmarks/agent-questions/agent_bench.py score /tmp/before
```

A run uses roughly 100k to 300k tokens per question of the subscription's weekly limit: run it
once before a change to the hub and once after, never on a schedule.
