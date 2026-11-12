#!/usr/bin/env python3
"""Run one real `claude -p` turn against the jc-platform-prod MCP only and keep its stream-json
transcript with the arrival time of each line (T-6818). The replay page renders these lines; nothing
is written by hand.

  term_run.py <name> "<prompt>" [--resume]
writes <name>.jsonl ({"t": seconds since start, "ev": <stream-json event>}) and prints the session id.
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = os.path.join(HERE, "session.id")
name, prompt = sys.argv[1], sys.argv[2]
cmd = ["claude", "-p", prompt, "--model", "sonnet", "--strict-mcp-config", "--mcp-config",
       os.path.join(HERE, "mcp.json"), "--allowedTools", "mcp__jc-platform-prod", "--tools", "",
       "--output-format", "stream-json", "--verbose"]
if "--resume" in sys.argv[3:]:
    cmd += ["--resume", open(SESSION).read().strip()]
t0 = time.time()
p = subprocess.Popen(cmd, cwd=os.path.join(HERE, "cwd"), stdout=subprocess.PIPE, text=True)
with open(os.path.join(HERE, name + ".jsonl"), "w") as out:
    for line in p.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype", "").startswith(("hook", "thinking")):
            continue
        out.write(json.dumps({"t": round(time.time() - t0, 2), "ev": ev}) + "\n")
        out.flush()
        if ev.get("session_id"):
            open(SESSION, "w").write(ev["session_id"])
        if ev.get("type") == "result":
            print("result", ev.get("subtype"), "cost", ev.get("total_cost_usd"))
sys.exit(p.wait())
