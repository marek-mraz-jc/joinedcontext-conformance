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
    ("Ako pripojím katalóg cez MCP do Clauda?", ["dev.joinedcontext.com/api/mcp"], False),
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
