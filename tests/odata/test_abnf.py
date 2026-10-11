"""The OASIS ABNF test cases, live (surfaces-conformance.md section 4, `test_abnf_cases_live`).

Every case of a rule that is a URL below the service root (`odataRelativeUri`, `resourcePath`) is
sent to view `all`. The cases name the file's own model (`Categories`, `Products`), which this
service does not hold, so a name the service lacks answers 404 before its syntax is judged, and
the exact failure offset is the parser's offline suite (Antares `odata_url_abnf_testcases`). What
the live run proves is what no offline suite can: the deployed service accepts no malformed URL,
refuses no valid one as malformed, and answers every refusal with the OData error object.

- a valid case: anything but 400, never a 5xx other than 501 `NotImplemented`;
- a `FailAt` case: 400 or 404, never a success;
- every 4xx and 5xx: the OData JSON error object.
"""

from __future__ import annotations

import yaml

from conftest import is_odata_error

RULES = ("odataRelativeUri", "resourcePath")
# One case (`$orderby=Name<TAB>asc`) holds a raw tab in a plain scalar, which PyYAML refuses.
TAB = ""


def cases(pins):
    text = (pins / "odata-abnf-testcases.yaml").read_text(encoding="utf-8").replace("\t", TAB)
    # BaseLoader: every scalar a string, as the OASIS runner reads its inputs
    return [c for c in yaml.load(text, Loader=yaml.BaseLoader)["TestCases"] if c["Rule"] in RULES]


def verdict(case, status: int, odata_error: bool) -> str | None:
    if status >= 400 and not odata_error:
        return "not the OData error object"
    if "FailAt" in case:
        return None if status in (400, 404) else "a malformed URL was not refused"
    if status == 400:
        return "a valid URL was refused as malformed"
    if status >= 500 and status != 501:
        return "a server error"
    return None


def test_abnf_cases_live(odata_url, http, pins):
    run = cases(pins)
    assert len(run) == 195, "the pinned file holds 195 cases of these rules"
    failures = []
    for case in run:
        resp = http.get(f"{odata_url}/{case['Input'].replace(TAB, chr(9))}")
        why = verdict(case, resp.status_code, is_odata_error(resp))
        if why:
            failures.append(f"{case['Name']} [{case['Input']}]: {resp.status_code}, {why}")
    assert not failures, "\n".join(failures)


def test_the_verdict_reads_the_case():
    valid, malformed = {"Input": "x"}, {"Input": "x", "FailAt": "3"}
    assert verdict(valid, 404, True) is None
    assert verdict(valid, 501, True) is None
    assert verdict(valid, 200, False) is None
    assert verdict(valid, 400, True)
    assert verdict(valid, 500, True)
    assert verdict(valid, 404, False)
    assert verdict(malformed, 400, True) is None
    assert verdict(malformed, 404, True) is None
    assert verdict(malformed, 200, False)
    assert verdict(malformed, 501, True)
