#!/usr/bin/env python3
"""T-6808: the create/read/delete walk through the jc-prod Portal MCP server, MCP tools only.

Uses T-6807's login (evidence/T-6807/mcp_login.py login first). Each call is appended to
steps.md (tool, arguments, HTTP code, answer summary; never a token). The walk stops at the
first step that is refused or would leave something it cannot remove.
"""
import json, os, sys, time
sys.path.insert(0, "/workspace/evidence/T-6807")
from mcp_login import Session  # noqa: E402

NAME = "mcp-admin-check"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "steps.md")
PROJECT = {"apiVersion": "joinedcontext.com/v1alpha1", "kind": "Project",
           "metadata": {"name": NAME}, "spec": {"organizationRef": "hel"}}


def summary(res):
    r = res.get("result") or {}
    sc = r.get("structuredContent")
    if sc is None:
        sc = res.get("error") or (r.get("content") or [{}])[0].get("text", "")
    s = json.dumps(sc, ensure_ascii=False)
    return s if len(s) < 600 else s[:600] + " ..."


def step(s, log, tool, args):
    res = s.call(tool, args)
    err = (res.get("result") or {}).get("isError") or "error" in res
    log.append(f"| {tool} | `{json.dumps(args)}` | {res['http']} | {'refused' if err else 'ok'}: {summary(res)} |")
    return res, err


def main():
    s = Session()
    log = [f"\n## Run {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}\n",
           f"initialize {s.init['http']} ({s.init.get('result', {}).get('serverInfo')})\n",
           "| tool | arguments | HTTP | answer |", "|---|---|---|---|"]
    # 1. Which projects exist in Organization hel (read; `project` names an existing one).
    step(s, log, "jc_organization_read", {"aspect": "projects", "project": "helsinki"})
    # 2. Dry-run the new Project: verdict, lane, planId.
    res, err = step(s, log, "jc_change_plan", {"project": NAME, "changes": [
        {"op": "create", "kind": "Project", "name": NAME, "manifest": PROJECT}]})
    plan = (res.get("result") or {}).get("structuredContent") or {}
    # 3. jc_change_apply would open a Change only a person approves in the Portal; MCP has no
    #    approve and no withdraw tool, so the walk stops here instead of leaving an open Change.
    tools = {t["name"] for t in s.rpc("tools/list", {})["result"]["tools"]}
    missing = sorted(t for t in ("jc_change_approve", "jc_change_withdraw", "jc_project_open") if t not in tools)
    log.append(f"\nStopped before jc_change_apply: plan lane `{plan.get('lane')}`, verdict ok "
               f"`{(plan.get('verdict') or {}).get('ok')}`; next says: {plan.get('next')!r}. "
               f"Not in tools/list ({len(tools)} tools): {', '.join(missing)}.\n")
    # 4. Nothing named mcp-admin-check exists afterwards.
    res, _ = step(s, log, "jc_organization_read", {"aspect": "projects", "project": "helsinki"})
    names = [i["name"] for i in ((res.get("result") or {}).get("structuredContent") or {}).get("items", [])]
    log.append(f"\nProjects after the walk: {names}; `{NAME}` present: {NAME in names}.\n")
    with open(OUT, "a") as f:
        f.write("\n".join(log) + "\n")
    print("\n".join(log))


if __name__ == "__main__":
    main()
