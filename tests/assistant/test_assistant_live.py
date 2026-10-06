"""The knowledge assistant on dev, asked like a resident asks (T-3055, AG-100, AG-102, AG-106).

Run against the live cluster only (`. tests/dev.env.sh assistant`): the seeded public
deployment `helsinki-public` answers from the road weather and events Endpoints through their
MCP surfaces, and each answer cites the Endpoint it read. Each question costs a few thousand
model tokens of the deployment's 300,000 a day. The conversation ids print, for the task's
record.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

BASE = os.environ.get("JC_ASSISTANT_URL", "")
PUBLIC_ID = os.environ.get("JC_ASSISTANT_PUBLIC_ID", "helsinki-public")


@pytest.fixture(scope="module")
def chat_url():
    if not BASE:
        pytest.fail("JC_ASSISTANT_URL is not set: source tests/dev.env.sh assistant")
    return f"{BASE.rstrip('/')}/api/v1/d/{PUBLIC_ID}/chat"


def ask(url, body, origin=None):
    """The answer's status and its events, as (name, data) in order."""
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as err:
        return err.code, []
    events = []
    for block in text.split("\n\n"):
        name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), None)
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "null")
        if name:
            events.append((name, json.loads(data)))
    return status, events


def endpoints_cited(events):
    citations = next((data for name, data in events if name == "citations"), [])
    return {c.get("endpoint") for c in citations if c.get("endpoint")}


@pytest.mark.parametrize(
    "question, endpoint",
    [
        ("What is the road weather at the Helsinki weather stations right now?", "helsinki-weather"),
        ("Which events are on in Helsinki in the coming days?", "helsinki-events"),
    ],
)
def test_a_question_is_answered_from_the_endpoint_that_holds_the_answer(chat_url, question, endpoint):
    status, events = ask(chat_url, {"message": question})
    assert status == 200, status
    names = [name for name, _ in events]
    assert names[0] == "conversation" and names[-1] == "done", names
    errors = [data for name, data in events if name == "error"]
    assert not errors, errors
    answer = next(data["text"] for name, data in events if name == "answer")
    assert "[" in answer, f"the answer cites nothing: {answer}"
    assert endpoint in endpoints_cited(events), (answer, events)
    print(f"conversation {events[0][1]['id']} {endpoint}: {answer[:200]}")


def test_a_page_on_another_site_is_refused(chat_url):
    status, _ = ask(chat_url, {"message": "Hello"}, origin="https://evil.example")
    assert status == 403
