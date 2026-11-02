"""Connector scenarios through the Portal assistant on dev, one fresh conversation per scenario (T-5962).

The person signs in to the Portal, types the same words `claude_code.py run` sends (live-world.yaml
filled in) into the docked assistant, and the run's events are read from the stream the dock reads.
A question the assistant asks gets its first option, three times at most. `<out>/<id>.json` keeps the
run's events without tool outputs longer than 2 KB, scrubbed; `<id>-1440.png` and `<id>-400.png` are
the Portal side.

    assistant_walk.py --out DIR --world live-world.yaml --password-file FILE --user EMAIL
        [--ids S-09,S-10] [--project t6025-full] [--scenarios scenarios.yaml]

`table DIR` prints one row per scenario (calls, refused, seconds, USD, answer head) with the verdicts
of `<out>/verdicts.yaml`, the same shape claude_code.py reads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import claude_code
import yaml

PORTAL = os.environ.get("PORTAL", "https://portal.dev.joinedcontext.com")
CHROME = "/ms-playwright/chromium-1243/chrome-linux64/chrome"
RUN_MS, QUIET_MS = 300_000, 20_000
KEEP = 2048

# Reads the run's stream with the page's own session and answers its questions, as the evals journey does.
FOLLOW = """async ([project, run, runMs, quietMs]) => {
  const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) || [])[1];
  const events = [];
  const stop = new AbortController();
  const deadline = setTimeout(() => stop.abort(), runMs);
  let quiet, asked = 0;
  const settle = () => { clearTimeout(quiet); quiet = setTimeout(() => stop.abort(), quietMs); };
  try {
    const res = await fetch(`/api/v1/projects/${project}/agent-runs/${run}/events`, {
      headers: { accept: "text/event-stream" }, signal: stop.signal });
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let end;
      while ((end = buffer.indexOf("\\n\\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const kind = /^event: ?(.*)$/m.exec(frame)?.[1];
        const data = frame.split("\\n").filter((l) => l.startsWith("data:")).map((l) => l.replace(/^data: ?/, "")).join("\\n");
        if (!kind || !data || kind === "lag") continue;
        const payload = JSON.parse(data);
        events.push({ kind, payload, at: Date.now() });
        if (kind === "question" && asked < 3) {
          asked += 1;
          clearTimeout(quiet);
          const given = (payload.options || [])[0]?.value;
          if (given === undefined) return events;
          await fetch(`/api/v1/projects/${project}/agent-runs/${run}/answers`, {
            method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf },
            body: JSON.stringify({ questionId: payload.questionId, answers: { answer: given } }) });
        } else if (kind !== "question" && events.some((e) => ["thought", "answer"].includes(e.kind))) {
          settle();
        } else if (kind === "question") {
          return events;
        }
      }
    }
  } catch (err) { /* the deadline or the quiet timer */ }
  finally { clearTimeout(deadline); clearTimeout(quiet); }
  return events;
}"""


def kept(events: list[dict]) -> list[dict]:
    """The events without long tool outputs, scrubbed."""
    text = json.dumps(events)
    out = json.loads(claude_code.scrub(text))
    for event in out:
        output = event["payload"].get("output")
        if isinstance(output, str) and len(output) > KEEP:
            event["payload"]["output"] = output[:KEEP] + "..."
        elif output is not None and len(json.dumps(output)) > KEEP:
            event["payload"]["output"] = json.dumps(output)[:KEEP] + "..."
    return out


def walk(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    world = yaml.safe_load(args.world.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    chosen = claude_code.scenarios(
        args.ids.split(",") if args.ids else None, args.scenarios
    )
    start = f"/projects/{args.project}/activity?lang=en"
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(
            f"{PORTAL}/login?redirect_to={start.replace('?', '%3F').replace('=', '%3D')}",
            wait_until="load",
        )
        page.fill("#username", args.user)
        page.fill("#password", args.password_file.read_text().strip())
        page.click("#kc-login")
        page.wait_for_url("**/projects/**", timeout=60_000)
        for scenario in chosen:
            one(page, scenario, args, world, start)
        browser.close()
    return 0


def one(
    page, scenario: dict, args: argparse.Namespace, world: dict, start: str
) -> None:
    sid = scenario["id"]
    text = claude_code.prompt(scenario, args.project, world)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(PORTAL + start, wait_until="load")
    page.evaluate("() => window.sessionStorage.removeItem('jc.assistant.run')")
    page.reload(wait_until="load")
    page.wait_for_timeout(1500)
    began = time.time()
    bubble = page.get_by_role("button", name="Open the assistant")
    if bubble.count():
        bubble.first.click()
    with page.expect_response(
        lambda r: (
            r.request.method == "POST" and r.url.endswith("/assistant/conversations")
        ),
        timeout=60_000,
    ) as started:
        composer = page.get_by_label("Ask the assistant").first
        composer.fill(text)
        composer.press("Enter")
    answer = started.value.json()
    if not started.value.ok:
        raise RuntimeError(
            f"{sid}: the conversation did not start: {started.value.status} {answer.get('detail')}"
        )
    run = answer["id"]
    events = page.evaluate(FOLLOW, [args.project, run, RUN_MS, QUIET_MS])
    seconds = time.time() - began
    page.wait_for_timeout(1000)
    page.screenshot(path=args.out / f"{sid}-1440.png")
    page.set_viewport_size({"width": 400, "height": 860})
    page.wait_for_timeout(800)
    page.screenshot(path=args.out / f"{sid}-400.png")
    usd = round(
        sum(e["payload"].get("costUsd", 0) for e in events if e["kind"] == "usage"), 4
    )
    record = {
        "id": sid,
        "prompt": text,
        "run": run,
        "seconds": round(seconds),
        "usd": usd,
        "events": kept(events),
    }
    (args.out / f"{sid}.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"{sid}: {sum(e['kind'] == 'tool' for e in events)} tool events, {seconds:.0f} s, USD {usd}",
        flush=True,
    )
    page.evaluate(
        f"() => fetch('/api/v1/projects/{args.project}/agent-runs/{run}/cancel', {{method: 'POST', headers: {{'x-csrf-token': (document.cookie.match(/__Host-jc_csrf=([^;]+)/) || [])[1]}}}})"
    )


def table(out: Path) -> str:
    verdicts = {}
    if (out / "verdicts.yaml").exists():
        verdicts = (
            yaml.safe_load((out / "verdicts.yaml").read_text(encoding="utf-8")) or {}
        )
    rows = [
        "| scenario | pass | tools | s | USD | note |",
        "|---|---|---:|---:|---:|---|",
    ]
    for path in sorted(out.glob("S-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        tools = sum(e["kind"] == "tool" for e in record["events"])
        verdict = verdicts.get(record["id"], {})
        passed = {True: "pass", False: "fail"}.get(verdict.get("pass"), "?")
        rows.append(
            f"| {record['id']} | {passed} | {tools} | {record['seconds']} | {record['usd']} | {verdict.get('note', '')} |"
        )
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if argv[:1] == ["table"]:
        print(table(Path(argv[1])))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--ids")
    parser.add_argument("--project", default="t6025-full")
    parser.add_argument("--scenarios", type=Path, default=claude_code.SCENARIOS)
    return walk(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
