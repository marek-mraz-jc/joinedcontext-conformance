"""T-3287 — a caller's own JSON-LD context never reaches the broker.

The gateway authorises the names the core context gives them: a grant of `description`, a form's
fields, a hidden attribute. A context of the caller's own (inline in the body, or named by a
`Link` header) would have the broker expand those names to other IRIs, so what the gateway allowed
and what the broker wrote or answered would differ. Every such request is refused with 400 and a
reason, before the broker is asked; the core context passes.

`SPACE_URL` and `TOKEN_STEWARD` cover the read and an authenticated write; `FORM_URL` (a public
form Endpoint's `…/ngsi-ld/v1`), `FORM_TYPE` and `FORM_FIELD` add the anonymous form create.
"""

import os
import uuid

import pytest
import requests

CORE = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.8.jsonld"
CONTEXT_REL = "http://www.w3.org/ns/json-ld#context"


def remapped(entity_type: str, field: str) -> tuple[str, dict]:
    """An entity whose context sends `field` to an IRI no grant names."""
    entity_id = f"urn:ngsi-ld:{entity_type}:t3287-{uuid.uuid4().hex[:12]}"
    return entity_id, {
        "id": entity_id,
        "type": entity_type,
        field: {"type": "Property", "value": "t3287"},
        "@context": [{field: f"https://example.org/t3287/{field}-elsewhere"}, CORE],
    }


def refused(response: requests.Response) -> None:
    assert response.status_code == 400, f"{response.status_code} {response.text[:300]}"
    assert "core context" in response.text, response.text[:300]


def test_t3287_a_read_naming_another_context_is_refused(get, granted_type: str):
    foreign = f'<https://example.org/t3287.jsonld>; rel="{CONTEXT_REL}"; type="application/ld+json"'
    refused(get("/entities", type=granted_type, headers={"Link": foreign}))
    core = f'<{CORE}>; rel="{CONTEXT_REL}"; type="application/ld+json"'
    assert get("/entities", type=granted_type, headers={"Link": core}).status_code == 200


def test_t3287_an_authenticated_write_whose_context_remaps_a_name_is_refused_and_nothing_is_written(
    http_session: requests.Session, space_url: str, token_steward: str, granted_type: str
):
    field = os.getenv("WRITE_FIELD", "name")
    entity_id, entity = remapped(granted_type, field)
    headers = {"Authorization": f"Bearer {token_steward}", "Content-Type": "application/ld+json"}
    refused(http_session.post(f"{space_url}/entities", json=entity, headers=headers, timeout=30))
    read = http_session.get(f"{space_url}/entities/{entity_id}", headers={"Authorization": f"Bearer {token_steward}"}, timeout=30)
    assert read.status_code == 404, f"the refused entity was written: {read.status_code}"


def test_t3287_a_public_form_create_whose_context_remaps_a_field_is_refused():
    form = os.getenv("FORM_URL")
    if not form:
        pytest.skip("environment variable FORM_URL not set")
    _, entity = remapped(os.getenv("FORM_TYPE", "Report"), os.getenv("FORM_FIELD", "description"))
    answer = requests.post(
        f"{form.rstrip('/')}/entities", json=entity, headers={"Content-Type": "application/ld+json"}, timeout=30
    )
    refused(answer)
