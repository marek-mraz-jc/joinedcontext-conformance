#!/usr/bin/env python3
"""The starting questions a public assistant offers, each asked N times on its environment (T-4769).

    python3 tests/assistant/eval_starters.py \
        --starters ../joinedcontext-deployment/tests/assistant_starters.json --env smartcity --runs 3 --out eval-starters

A card a visitor clicks has to be right every time (owner 2026-10-19T20:45Z: "all these tasks that
are prefiled should be 100 percent correct"). The starters file lives beside the seeds that carry
the cards (joinedcontext-deployment tests/assistant_starters.json, held to them by
tests/test_assistant_starters.py there); one row per card and language:

- `must`: groups of alternatives, every group in the answer (eval_bb's `norm`, so 7.30 is 7:30); a
  number is a whole number of the answer, so 20 is not found in 2026 and 1 037 is 1037;
- `links`: human pages (a CKAN dataset page or a page of the body's site), at least one of them
  cited or written in the answer; `{domain}` is the environment's domain;
- `live` (optional): `{url, attr}`, an NGSI-LD entity read at ask time whose attribute value, to
  one decimal, is in the answer: a measured value has no fixed truth;
- `reference` and `source`: the answer written from the data and where it was read.

The questions go to the visitors' own deployments, anonymous, under their spend cap: one visitor
address may spend a tenth of a deployment's day (chat/mod.rs VISITOR_COST_SHARE), so a 429 titled
"Visitor Limit Reached" stops the run and names the starters left, and nothing works around the cap.
Any other 429 is the per-minute limit: the run waits its Retry-After and asks again. A cached answer
is what every visitor gets for the same card for an hour (routed.rs CACHE_LIFE), so it counts as a
run; `--between 3900` puts the runs past the cache, each a fresh answer. The results file is written
after every answer, so a stopped run keeps what it got.

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
NUMBER = re.compile(r"^\d+(?:[\s.,]\d{3})*$")
NUMBERS = re.compile(r"(?<![\d.,])\d+(?:[ \u00a0\u202f.,]\d{3})*(?![\d])")
SHARE_SPENT = "Visitor Limit Reached"


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


def holds(answer, alt):
    """Whether `alt` is in the answer: a whole number of it when `alt` is a number."""
    if NUMBER.match(alt):
        want = re.sub(r"\D", "", alt)
        return any(re.sub(r"\D", "", n) == want for n in NUMBERS.findall(answer))
    return norm(alt) in norm(answer)


def linked(answer, citations, links):
    """Whether one of the expected human pages is cited or written in the answer."""
    seen = [u.split("#")[0].rstrip("/") for u in citations] + [u.rstrip("/.,") for u in LINKS.findall(answer)]
    return any(any(u == want.rstrip("/") for u in seen) for want in links)


def judge(row, answer, citations, live=None):
    """(right, what is missing) for one answer to one starter."""
    missing = [group for group in row["must"] if not any(holds(answer, alt) for alt in group)]
    if live is not None and not has_value(answer, live):
        missing.append([f"live {row['live']['attr']}={live}"])
    if not linked(answer, citations, row["links"]):
        missing.append(["a link to " + " or ".join(row["links"])])
    return not missing, missing


def expand(row, domain):
    text = json.dumps(row, ensure_ascii=False).replace("{domain}", domain)
    return json.loads(text)


def asked(base, row, origin, tries=5):
    """The answer's events, waiting out the per-minute limit; None when the day's share is spent."""
    for _ in range(tries):
        try:
            return ask(base, row["assistant"], origin, row["q"])
        except urllib.error.HTTPError as err:
            if err.code != 429:
                raise
            retry = err.headers.get("Retry-After") or "60"
            if SHARE_SPENT in err.read().decode(errors="replace"):
                print(f"429 {SHARE_SPENT} from {row['assistant']}: retry after {retry} s", file=sys.stderr)
                return None
            time.sleep(min(int(retry) if retry.isdigit() else 60, 120) + 1)
    raise RuntimeError(f"{row['id']}: still limited after {tries} tries")


def save(out, results):
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))


def run(rows, env, runs, out, between=0):
    base = f"https://assistant.{DOMAINS[env]}"
    results = []
    for n in range(1, runs + 1):
        if n > 1:
            time.sleep(between)
        for i, row in enumerate(rows):
            origin = ORIGINS[(row["assistant"], env)]
            answered = asked(base, row, origin)
            if answered is None:
                print(f"Not asked in run {n}: {[r['q'] for r in rows[i:]]}", file=sys.stderr)
                return results, 2
            events, ttft, total = answered
            r = record({"id": row["id"]}, events, ttft, total, None, {})
            live = live_value(row["live"]["url"], row["live"]["attr"]) if row.get("live") else None
            r["right"], r["missing"] = judge(row, r["answer"], r["citations"], live)
            r.update(run=n, q=row["q"], assistant=row["assistant"], live=live)
            r.pop("events", None)
            results.append(r)
            save(out, results)
            print(f"{row['id']} run {n}: {'ok' if r['right'] else 'ko ' + json.dumps(r['missing'], ensure_ascii=False)}")
            time.sleep(7)
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
    # A number is a whole number of the answer, its thousands grouped any way.
    count = dict(row, must=[["1037", "1 037"], ["20"]])
    assert judge(count, "Kraj spravuje 1 037 mostov, 20 z nich pred rokom 1900.", page)[0]
    assert judge(count, "1,037 bridges; 20 built before 1900.", page)[0]
    assert not judge(count, "1 037 mostov v roku 2026.", page)[0]
    assert not judge(count, "10 370 mostov, 20.", page)[0]
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
    parser.add_argument("--between", type=int, default=0, help="seconds between runs; 3900 passes the answer cache")
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
    _, code = run(rows, args.env, args.runs, args.out, args.between)
    return code


if __name__ == "__main__":
    sys.exit(main())
