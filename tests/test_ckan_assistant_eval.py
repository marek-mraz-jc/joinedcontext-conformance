"""T-3153: the CKAN assistant eval's scoring (benchmarks/ckan-assistant/ckan_eval.py)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "ckan-assistant"
spec = importlib.util.spec_from_file_location("ckan_eval", ROOT / "ckan_eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

Q = {"id": "x", "expect": ["CC[ -]?BY"], "forbid": ["\\b\\d{3,} ?€"], "cite": "public-air"}
CITE = [{"url": "https://data.dev.joinedcontext.com/dataset/public-air"}]


def got(answer, citations=CITE, error=None):
    return {"answer": answer, "citations": citations, "error": error, "done": {"costUsd": 0.004}}


def test_the_stream_is_read_answer_citations_and_done():
    lines = ["event: answer", 'data: {"text":"Licencia "}', "", "event: answer", 'data: {"text":"CC BY [1]"}',
             "event: citations", 'data: [{"n":1,"url":"u/public-air"}]', "event: done", 'data: {"costUsd":0.0041}',
             "event: answer", "data: not json"]
    out = ev.read_stream(lines)
    assert out["answer"] == "Licencia CC BY [1]"
    assert out["citations"][0]["url"] == "u/public-air"
    assert out["done"]["costUsd"] == 0.0041


def test_a_right_cited_answer_in_the_asked_language_passes():
    assert ev.judge(Q, "sk", got("Pre tento dataset platí licencia CC BY 4.0 [1].")) == []
    assert ev.judge(Q, "en", got("The dataset is published under CC BY 4.0 [1].")) == []


def test_every_way_an_answer_is_wrong_is_named():
    assert ev.judge(Q, "sk", got("Pre tento dataset platí licencia ODbL.")) == ["misses /CC[ -]?BY/"]
    assert ev.judge(Q, "en", got("The dataset is CC BY.", citations=[])) == ["no citation of public-air"]
    assert ev.judge(Q, "en", got("Pre dataset platí CC BY, nájdete ho v katalógu.")) == ["answers in sk"]
    assert "says" in ev.judge(Q, "en", got("The salary is CC BY 1500 € for the dataset."))[0]
    assert ev.judge(Q, "en", got("", error={"status": 504, "title": "Answer Took Too Long"})) == ["error 504: Answer Took Too Long"]
    assert ev.judge(Q, "en", got("   ")) == ["no answer"]


def test_the_table_counts_passes_and_spend_from_the_results():
    rows = [{"id": "a", "lang": "sk", "wrong": [], "citations": CITE, "cost": 0.004},
            {"id": "a", "lang": "en", "wrong": ["misses /x/"], "citations": [], "cost": 0.006}]
    t = ev.table(rows)
    assert "PASS 1/2 (50 %); spend $0.0100" in t
    assert "| a | en | misses /x/ | - | 0.0060 |" in t


def test_every_question_is_asked_in_both_languages_with_an_expectation():
    qs = json.loads((ROOT / "questions.json").read_text())["questions"]
    assert len(qs) == 20 and len({q["id"] for q in qs}) == 20
    assert {q["org"] for q in qs} == {"banskabystrica", "bbsk"}
    for q in qs:
        assert q["sk"] and q["en"] and q["expect"], q["id"]
