#!/usr/bin/env python3
"""Hold tests/etsi/expected_view_refusals.json to its shape (T-3928).

The file is the `owed-refusals.json` of the first green nightly `etsi-full-restricted` (E4) of
Antares `surfaces-conformance.yml`, unchanged, with the run it came from. Every entry is a TP
that passes at the root (E1) and is refused through view `etsi-restricted`, and every entry
says why the view owes that refusal. Antares' generator (`dev/etsi-view-diff.py`) names one of
two grounds: an ADR-0023 rule (an operation the view does not offer, a Subscription made at the
root, an NGSILD-Tenant) or threat V22 (an entity map, never served through a view). An entry
with neither ground is a difference nobody explained, so this check fails on it.

Usage: check_view_refusals.py [FILE]    check FILE (default: the committed file)
       check_view_refusals.py --selftest
"""
import json
import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).with_name("expected_view_refusals.json")
GROUND = re.compile(r"\(ADR-0023 rules? \d+(?: and \d+)*\)|\(threat V\d+\)")
SOURCE = ("repository", "workflow", "job", "run", "branch", "artifact")


def problems(doc) -> list[str]:
    found: list[str] = []
    if not isinstance(doc, dict):
        return ["the file is not a JSON object"]
    source = doc.get("source")
    if not isinstance(source, dict) or any(not source.get(k) for k in SOURCE):
        found.append(f"source must name {', '.join(SOURCE)}")
    elif not isinstance(source["run"], int):
        found.append("source.run must be the run id, a number")
    if not isinstance(doc.get("view"), str) or not doc["view"]:
        found.append("view must name the view the refusals are owed through")
    refusals = doc.get("refusals")
    if not isinstance(refusals, list) or not refusals:
        return found + ["refusals must be a non-empty list"]
    seen: set[str] = set()
    for n, entry in enumerate(refusals):
        tp = entry.get("tp") if isinstance(entry, dict) else None
        where = tp or f"entry {n}"
        if not isinstance(tp, str) or "::" not in tp:
            found.append(f"{where}: tp must read <suite>.robot::<test>")
            continue
        if tp in seen:
            found.append(f"{tp}: listed twice")
        seen.add(tp)
        if (entry.get("e1"), entry.get("view")) != ("PASS", "FAIL"):
            found.append(f"{tp}: an owed refusal passes at the root and fails through the view")
        owed = entry.get("owed")
        if not isinstance(owed, list) or not owed or not all(isinstance(o, str) for o in owed):
            found.append(f"{tp}: owed must list the reasons")
        elif not any(GROUND.search(o) for o in owed):
            found.append(f"{tp}: no reason names an ADR-0023 rule or a threat")
    return found


def selftest() -> int:
    good = {
        "source": {"repository": "r", "workflow": "w.yml", "job": "j", "run": 1, "branch": "dev", "artifact": "a"},
        "view": "etsi-restricted",
        "refusals": [
            {"tp": "A.robot::a", "e1": "PASS", "view": "FAIL", "owed": ["GET x: y (ADR-0023 rule 4)"]},
            {"tp": "B.robot::b", "e1": "PASS", "view": "FAIL", "owed": ["GET q: an entity map (threat V22)"]},
            {"tp": "C.robot::c", "e1": "PASS", "view": "FAIL", "owed": ["GET t: lists only the names the view shows (ADR-0023 rules 2 and 3)"]},
        ],
    }
    assert problems(good) == [], problems(good)
    cases = {
        "no ground": ({"owed": ["GET x: a view refused it"]}, "no reason names"),
        "no reasons": ({"owed": []}, "owed must list"),
        "passes through the view": ({"view": "PASS"}, "fails through the view"),
        "a bare tp": ({"tp": "A.robot"}, "tp must read"),
    }
    for name, (change, said) in cases.items():
        broken = json.loads(json.dumps(good))
        broken["refusals"][0].update(change)
        assert any(said in p for p in problems(broken)), (name, problems(broken))
    twice = json.loads(json.dumps(good))
    twice["refusals"].append(dict(twice["refusals"][0]))
    assert any("listed twice" in p for p in problems(twice)), problems(twice)
    nameless = json.loads(json.dumps(good))
    nameless["source"]["run"] = "latest"
    assert any("source.run" in p for p in problems(nameless)), problems(nameless)
    assert problems([]) == ["the file is not a JSON object"]
    assert any("non-empty" in p for p in problems(dict(good, refusals=[])))
    print("selftest: ok")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 1
    found = problems(doc)
    for p in found:
        print(f"{path}: {p}", file=sys.stderr)
    if not found:
        print(f"{path}: {len(doc['refusals'])} owed refusals through view {doc['view']}, run {doc['source']['run']}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
