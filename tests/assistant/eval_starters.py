#!/usr/bin/env python3
"""The starting questions a public assistant offers, each asked N times on its environment (T-4769).

    python3 tests/assistant/eval_starters.py \
        --starters ../joinedcontext-deployment/tests/assistant_starters.json --env smartcity --runs 3 --out eval-starters

A card a visitor clicks has to be right every time (owner 2026-10-19T20:45Z: "all these tasks that
are prefiled should be 100 percent correct"). The starters file lives beside the seeds that carry
the cards (joinedcontext-deployment tests/assistant_starters.json, held to them by
tests/test_assistant_starters.py there); one row per card and language:

- `must`: groups of alternatives, every group in the answer (eval_bb's `norm`, so 7.30 is 7:30);
- `links`: human pages (a CKAN dataset page or a page of the body's site), at least one of them
  cited or written in the answer; `{domain}` is the environment's domain;
- `live` (optional): `{url, attr}`, an NGSI-LD entity read at ask time whose attribute value, to
  one decimal, is in the answer: a measured value has no fixed truth;
- `reference` and `source`: the answer written from the data and where it was read.

The questions go to the visitors' own deployments, anonymous, under their spend cap: one visitor
address may spend a tenth of a deployment's day (chat/mod.rs VISITOR_COST_SHARE), so a 429 stops
the run and names the starters left, and nothing works around the cap. A cached answer is what
every visitor gets for the same card, so it counts as a run.

Exit 0 when every starter is right in every run; 1 otherwise; 2 on a usage error or a 429.
`--selftest` checks the grading with no network.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from eval_bb import ask, norm, record

DOMAINS = {"smartcity": "smartcity.joinedcontext.com", "dev": "dev.joinedcontext.com"}
ORIGINS = {
    ("data", "smartcity"): "https://opendata.smartcity.joinedcontext.com",
    ("data", "dev"): "https://opendata.dev.joinedcontext.com",
    ("bbsk-demo", "smartcity"): "https://bbsk.smartcity.joinedcontext.com",
    ("bbsk-demo", "dev"): "https://bbsk-demo.apps.dev.joinedcontext.com",
}
LINKS = re.compile(r"https?://[^\s)\]>\"'`]+")


def live_value(url, attr):
    """The attribute's value of the entity at `url`, read now (keyValues), or None."""
    request = urllib.request.Request(f"{url}?format=keyValues&pick={attr}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response).get(attr)
    return value if isinstance(value, (int, float)) else None


def has_value(answer, value):
    """Whether `value` is in the answer to one decimal, with a point or a comma, rounded or cut."""
    shown = {f"{value:.1f}", f"{int(value * 10) / 10:.1f}", f"{value:.2f}"}
    text = re.sub(r"\[\d+(?:,\s*\d+)*\]", " ", answer)
    patterns = [re.escape(s).replace(r"\.", "[.,]") for s in shown]
    return any(re.search(rf"(?<![\d.,]){p}(?!\d)", text) for p in patterns)


def linked(answer, citations, links):
    """Whether one of the expected human pages is cited or written in the answer."""
    seen = [u.split("#")[0].rstrip("/") for u in citations] + [u.rstrip("/.,") for u in LINKS.findall(answer)]
    return any(any(u == want.rstrip("/") for u in seen) for want in links)


def judge(row, answer, citations, live=None):
    """(right, what is missing) for one answer to one starter."""
    missing = [group for group in row["must"] if not any(norm(alt) in norm(answer) for alt in group)]
    if live is not None and not has_value(answer, live):
        missing.append([f"live {row['live']['attr']}={live}"])
    if not linked(answer, citations, row["links"]):
        missing.append(["a link to " + " or ".join(row["links"])])
    return not missing, missing


def expand(row, domain):
    text = json.dumps(row, ensure_ascii=False).replace("{domain}", domain)
    return json.loads(text)


def run(rows, env, runs, out):
    base = f"https://assistant.{DOMAINS[env]}"
    results = []
    for n in range(1, runs + 1):
        for i, row in enumerate(rows):
            origin = ORIGINS[(row["assistant"], env)]
            try:
                events, ttft, total = ask(base, row["assistant"], origin, row["q"])
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    left = [r["q"] for r in rows[i:]]
                    print(f"429 from {row['assistant']}: the visitor's share of the day is spent; retry after "
                          f"{err.headers.get('Retry-After')} s. Not asked in run {n}: {left}", file=sys.stderr)
                    return results, 2
                raise
            r = record({"id": row["id"]}, events, ttft, total, None, {})
            live = live_value(row["live"]["url"], row["live"]["attr"]) if row.get("live") else None
            r["right"], r["missing"] = judge(row, r["answer"], r["citations"], live)
            r.update(run=n, q=row["q"], assistant=row["assistant"], live=live)
            r.pop("events", None)
            results.append(r)
            print(f"{row['id']} run {n}: {'ok' if r['right'] else 'ko ' + json.dumps(r['missing'], ensure_ascii=False)}")
            time.sleep(2)
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return results, 0 if all(r["right"] for r in results) else 1


def selftest():
    row = {
        "id": "s", "assistant": "bbsk-demo", "lang": "sk", "q": "?",
        "must": [["8:00"], ["15:00"], ["Námestie SNP 23"]],
        "links": ["https://opendata.example/dataset/bbsk-uradne-hodiny-c912ed"],
    }
    good = "Podateľňa je otvorená od 8.00 do 15.00, sídlo Námestie SNP 23 [1]."
    page = ["https://opendata.example/dataset/bbsk-uradne-hodiny-c912ed"]
    assert judge(row, good, page) == (True, [])
    # A machine address is no page a person can read.
    right, missing = judge(row, good, ["https://api.example/api/endpoint/x/schema/index.json"])
    assert not right and "a link to" in missing[0][0]
    # The link may be written in the answer itself.
    assert judge(row, good + " https://opendata.example/dataset/bbsk-uradne-hodiny-c912ed.", [])[0]
    right, missing = judge(row, "Od 8:00, Námestie SNP 23.", page)
    assert not right and missing == [["15:00"]]
    # A measured value, to one decimal, with a comma or a point.
    assert has_value("PM10 je 14,64 µg/m³ [2].", 14.637) and has_value("PM10 14.6", 14.637)
    assert not has_value("PM10 je 4,6 µg/m³", 14.637) and not has_value("PM10 je 114,6", 14.637)
    live_row = dict(row, must=[["PM10"]], live={"url": "-", "attr": "pm10"})
    assert judge(live_row, "PM10 14,6 µg/m³", page, live=14.637)[0]
    assert not judge(live_row, "PM10 27,6 µg/m³", page, live=14.637)[0]
    assert expand({"links": ["https://opendata.{domain}/dataset/x"]}, "dev.joinedcontext.com") == {
        "links": ["https://opendata.dev.joinedcontext.com/dataset/x"]
    }
    print("eval_starters selftest: ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--starters", help="joinedcontext-deployment tests/assistant_starters.json")
    parser.add_argument("--env", choices=sorted(DOMAINS), default="smartcity")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--assistant", help="only this publicId")
    parser.add_argument("--out")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.starters or args.runs < 1:
        parser.print_usage(sys.stderr)
        return 2
    spec = json.loads(Path(args.starters).read_text())
    rows = [expand(r, DOMAINS[args.env]) for r in spec["starters"] if not args.assistant or r["assistant"] == args.assistant]
    _, code = run(rows, args.env, args.runs, args.out)
    return code


if __name__ == "__main__":
    sys.exit(main())
