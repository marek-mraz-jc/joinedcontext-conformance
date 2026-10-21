#!/usr/bin/env python3
"""The run page summary of the standards suite (T-5044): one row per Endpoint and suite.

summary.py RESULTS.tsv TARGET API     Markdown for $GITHUB_STEP_SUMMARY and summary.md
summary.py --self-test
"""

from __future__ import annotations

import csv
import io
import sys

MARK = {"pass": "✅ pass", "fail": "❌ fail", "n/a": "➖ not served"}


def cell(text: str) -> str:
    return text.replace("|", "\\|").strip() or " "


def render(rows: list[dict], target: str, api: str) -> str:
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in MARK}
    out = [
        f"# Standards suite on {target}",
        "",
        f"{api}: {counts['pass']} pass, {counts['fail']} fail, {counts['n/a']} not served.",
        "",
        "| Endpoint | Suite | Verdict | Detail | Report |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['endpoint']}` | {cell(r['suite'])} | {MARK.get(r['verdict'], r['verdict'])}"
            f" | {cell(r['detail'])} | {cell(r['log'])} |"
        )
    out += ["", f"**Verdict: {'RED' if counts['fail'] else 'GREEN'}**", ""]
    return "\n".join(out)


def read(text: str) -> list[dict]:
    return list(
        csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    )


def self_test() -> None:
    tsv = (
        "endpoint\tsuite\tverdict\tdetail\tlog\n"
        "abc\tngsi-ld\tpass\t9 passed in 1.6s\tabc/ngsi-ld/pytest.txt\n"
        "abc\tws\tfail\tFAILED a|b 1 failed, 6 passed\tabc/ws/pytest.txt\n"
        "abc\tsta\tn/a\tthe Endpoint does not serve sta\t\n"
    )
    md = render(read(tsv), "prod", "https://api.example")
    assert "1 pass, 1 fail, 1 not served" in md, md
    assert "| `abc` | ws | ❌ fail | FAILED a\\|b 1 failed, 6 passed |" in md, md
    assert md.rstrip().endswith("**Verdict: RED**"), md
    green = render(read(tsv.split("abc\tws")[0]), "dev", "x")
    assert "**Verdict: GREEN**" in green, green
    print("summary.py self-test ok")


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        self_test()
        return 0
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        print(render(read(f.read()), argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
