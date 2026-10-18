"""data.dev's one general assistant on dev, asked as a catalogue visitor asks (T-4342, AG-114).

Run against the live cluster only (`. tests/dev.env.sh assistant`): every page of data.dev frames
`/d/data`, which knows the whole catalogue (its CKAN records), carries a portal guide in its
instructions and reads every publisher's public Endpoints. Three questions are answered from the
catalogue alone, one from the guide, and the owner's two data questions name each publisher.
Each question spends a few cents of the deployment's $1 a day; the conversation ids print, for
the task's record.
"""

import os

import pytest

from test_assistant_live import BASE, ask

ORIGIN = "https://data.dev.joinedcontext.com"


@pytest.fixture(scope="module")
def chat_url():
    if not BASE:
        pytest.fail("JC_ASSISTANT_URL is not set: source tests/dev.env.sh assistant")
    return f"{BASE.rstrip('/')}/api/v1/d/{os.environ.get('JC_DATA_ASSISTANT_PUBLIC_ID', 'data')}/chat"


def answered(chat_url, question):
    """The answer's text (lower case) and its citations, after the stream's own checks."""
    status, events = ask(chat_url, {"message": question}, origin=ORIGIN)
    assert status == 200, status
    names = [name for name, _ in events]
    assert names[0] == "conversation" and names[-1] == "done", names
    assert not [data for name, data in events if name == "error"], events
    answer = next(data["text"] for name, data in events if name == "answer")
    citations = next((data for name, data in events if name == "citations"), [])
    print(f"conversation {events[0][1]['id']}: {question} -> {answer[:300]}")
    return answer.lower(), citations


def datasets_cited(citations):
    return {c["url"] for c in citations if "/dataset/" in (c.get("url") or "")}


# (question, alternatives the answer must hold one of, whether a dataset page must be cited)
CATALOGUE = [
    # A topic search: the catalogue lists transport data of more than one publisher.
    ("Aké dáta máte o doprave?", ["doprav", "železni", "zastáv", "nehod"], True),
    # A publisher's dataset list.
    ("Ktoré datasety zverejňuje mesto Žilina?", ["žilina — verejné dáta", "zilina-verejne"], True),
    # A licence, as the dataset's record states it.
    ("Pod akou licenciou je dataset Žilina — verejné dáta?", ["creative commons", "cc by", "cc-by"], True),
    # The portal guide, not the catalogue: the one MCP address and where Claude takes it.
    ("Ako pripojím katalóg cez MCP do Clauda?", ["api.dev.joinedcontext.com/api/mcp"], False),
]


@pytest.mark.parametrize("question, must, cites_dataset", CATALOGUE)
def test_the_catalogue_and_the_guide_answer_without_a_crawl(chat_url, question, must, cites_dataset):
    answer, citations = answered(chat_url, question)
    assert any(word in answer for word in must), answer
    if cites_dataset:
        assert datasets_cited(citations), (answer, citations)


@pytest.mark.parametrize("question, publishers", [
    ("Aká je kvalita ovzdušia v Žiline a v Banskej Bystrici?", [("žilin",), ("banská bystrica", "banskej bystric")]),
    ("Koľko obyvateľov má Banskobystrický kraj?", [("banskobystrick",)]),
])
def test_the_owners_data_questions_name_the_publisher(chat_url, question, publishers):
    answer, citations = answered(chat_url, question)
    for words in publishers:
        assert any(word in answer for word in words), (words, answer)
    assert any(c.get("endpoint") for c in citations) or datasets_cited(citations), (answer, citations)


def test_another_site_is_refused(chat_url):
    status, _ = ask(chat_url, {"message": "Dobrý deň"}, origin="https://www.zilina.sk")
    assert status == 403


# T-4359: the open-data set in the hourly batch, on an eval copy of `data` so the visitors' day is
# never spent. Four questions an hour, the chunk turning with the UTC hour, cover the whole set
# (the unknowable ones too) about twice a day at about two cents an hour; JC_OPENDATA_ALL=1 asks all of it.
import json as _json
from datetime import datetime, timezone

import eval_opendata

SET = _json.loads(eval_opendata.QUESTIONS.read_text())
CHUNK = 4


def _this_hour():
    if os.environ.get("JC_OPENDATA_ALL") == "1":
        return SET["questions"]
    chunks = [SET["questions"][i:i + CHUNK] for i in range(0, len(SET["questions"]), CHUNK)]
    return chunks[datetime.now(timezone.utc).hour % len(chunks)]


@pytest.mark.parametrize("q", _this_hour(), ids=lambda q: q["id"])
def test_the_open_data_set_answers_the_live_value(q):
    if not BASE:
        pytest.fail("JC_ASSISTANT_URL is not set: source tests/dev.env.sh assistant")
    public_id = os.environ.get("JC_OPENDATA_EVAL_ID", "data-eval-gemini")
    assert eval_opendata.eval_deployment(public_id), public_id
    truth = eval_opendata.read_truth(q)
    events, ttft, total = eval_opendata.ask(BASE, public_id, ORIGIN, q["q"], q.get("history", ()))
    row = eval_opendata.record(q, events, ttft, total, "", {})
    assert not row["error"], row["error"]
    if q["kind"] == "unknowable":
        grade, invented = eval_opendata.grade_unknowable(q, truth, row, SET["known"])
        assert not invented, (row["answer"], invented)
        wrong_number = False
    elif q["kind"] == "direct":
        grade, wrong_number = eval_opendata.grade_direct(row, SET["canary"])
    else:
        grade, wrong_number = eval_opendata.grade(q, truth, row["answer"], SET["canary"])
    print(f"{q['id']} {grade} ttft {ttft} total {total}: {row['answer'][:200]!r} (truth {truth})")
    assert not eval_opendata.has(row["answer"], SET["canary"]), row["answer"]
    outside = [e for e in eval_opendata.named_endpoints(row["events"]) if e not in eval_opendata.catalogue_endpoints()]
    assert not outside, outside
    assert not wrong_number, (row["answer"], truth)
    assert grade == "correct", (row["answer"], truth)
