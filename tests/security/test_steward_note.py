"""The one attribute of a published row a person may write, and the refusal on every other one.

The two Slovak raw spaces hold what a publisher published: every figure is written by a pipeline
and replaced by its next run. `stewardNote` (T-2434, `statistical-observation` 1.1.0) is the one
attribute no pipeline touches, and the grant that opens it names that attribute alone, so the
same person who may annotate a row may not correct its number. These cases are what keeps the two
apart: a grant that grew a second attribute, an endpoint that stopped projecting the write, or a
write guard that trimmed a refused write instead of refusing it whole would each pass silently
otherwise (EP-14, AP-62, GW16, GW17).

They need a surface, a row on it and two identities, and skip without them:

  NOTE_URL         the raw space's NGSI-LD base through its own endpoint,
                   e.g. $BASE/api/endpoint/$SLUG/ngsi-ld/v1
  NOTE_ENTITY_ID   a StatisticalObservation of that space
  TOKEN_STEWARD    a token of the person the note grant names
  TOKEN_VIEWER     a token of somebody the note grant does not name (the refusal case)
"""

import os
import typing

import pytest
import requests

NOTE = "stewardNote"
#: What the note grant does not reach: the publisher's own number.
FIGURE = "value"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"environment variable {name} not set")
    return value


@pytest.fixture(scope="session")
def note_url() -> str:
    return _require_env("NOTE_URL").rstrip("/")


@pytest.fixture(scope="session")
def note_entity_id() -> str:
    return _require_env("NOTE_ENTITY_ID")


@pytest.fixture
def patch_attrs(
    http_session: requests.Session, note_url: str, note_entity_id: str
) -> typing.Callable[..., requests.Response]:
    """`PATCH /entities/{id}/attrs`, the one write the records applications make (AP-62)."""

    def _patch(body: dict, token: typing.Optional[str] = None) -> requests.Response:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return http_session.patch(
            f"{note_url}/entities/{note_entity_id}/attrs", json=body, headers=headers, timeout=30
        )

    return _patch


@pytest.fixture
def read_entity(
    http_session: requests.Session, note_url: str, note_entity_id: str
) -> typing.Callable[..., dict]:
    def _read(token: str) -> dict:
        res = http_session.get(
            f"{note_url}/entities/{note_entity_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert res.status_code == 200, f"the row could not be read back: {res.status_code}"
        return res.json()

    return _read


def value_of(entity: dict, attribute: str):
    """The value of an NGSI-LD attribute, whichever projection the surface answered in."""
    held = entity.get(attribute)
    return held.get("value") if isinstance(held, dict) else held


def test_ep14_the_steward_writes_the_note_and_the_row_carries_it(
    patch_attrs: typing.Callable[..., requests.Response],
    read_entity: typing.Callable[..., dict],
    token_steward: str,
):
    """EP-14, AP-62 — the grant opens one attribute and the write of it succeeds through the
    endpoint, so the application that offers the field is offering something that works."""
    written = "Checked against the publisher's table (conformance)."
    res = patch_attrs({NOTE: {"type": "Property", "value": written}}, token=token_steward)
    assert res.status_code in (200, 204), f"the note was refused: {res.status_code} {res.text:.200}"
    assert value_of(read_entity(token_steward), NOTE) == written


def test_gw17_the_same_person_may_not_write_a_published_figure(
    patch_attrs: typing.Callable[..., requests.Response],
    read_entity: typing.Callable[..., dict],
    token_steward: str,
):
    """GW16, GW17 — the grant names the attribute, not the type: a write to the number is the
    gateway's own refusal, and the number is still the publisher's afterwards."""
    before = value_of(read_entity(token_steward), FIGURE)
    res = patch_attrs({FIGURE: {"type": "Property", "value": 999_999.0}}, token=token_steward)
    assert res.status_code == 403, f"a published figure was writable: {res.status_code}"
    # RFC 7807, which is what the application shows beside the field it was refused on.
    assert "application/problem+json" in res.headers.get("Content-Type", "")
    assert res.json().get("status") == 403
    assert value_of(read_entity(token_steward), FIGURE) == before


def test_gw17_a_write_of_the_note_and_a_figure_together_is_refused_whole(
    patch_attrs: typing.Callable[..., requests.Response],
    read_entity: typing.Callable[..., dict],
    token_steward: str,
):
    """GW17 — a write touching anything outside the grant is denied whole, never trimmed to the
    part that was granted. A trimmed write would report success and save half of it."""
    before = value_of(read_entity(token_steward), NOTE)
    res = patch_attrs(
        {
            NOTE: {"type": "Property", "value": "Trimmed?"},
            FIGURE: {"type": "Property", "value": 888_888.0},
        },
        token=token_steward,
    )
    assert res.status_code == 403
    assert value_of(read_entity(token_steward), NOTE) == before, "half the write landed"


@pytest.mark.xfail(
    strict=True,
    reason="DM-27 is open in docs/Requirements/compliance-matrix.md: the gateway serves the "
    "published JSON Schema but does not validate writes against it, so the model's bound is "
    "held by the application alone. The day it is enforced this case passes and the marker goes.",
)
def test_dm27_a_note_the_model_refuses_never_reaches_the_row(
    patch_attrs: typing.Callable[..., requests.Response],
    read_entity: typing.Callable[..., dict],
    token_steward: str,
):
    """DM-27 — the note is free text with a bound: the model's `^[^<>]{0,500}$` belongs to the
    space's published schema, and a create or update that breaks it is a 400 naming the keyword,
    whichever application sent it."""
    before = value_of(read_entity(token_steward), NOTE)
    res = patch_attrs(
        {NOTE: {"type": "Property", "value": "<script>alert(1)</script>"}}, token=token_steward
    )
    assert res.status_code in (400, 422), f"the model's own bound was not applied: {res.status_code}"
    assert value_of(read_entity(token_steward), NOTE) == before


def test_ep14_a_person_the_grant_does_not_name_is_refused_on_the_note(
    patch_attrs: typing.Callable[..., requests.Response],
    token_viewer: str,
):
    """EP-14 — the note is open to the person the Policy names and to nobody else; a reader of
    the same space is refused on the same field, which is what the application shows them."""
    res = patch_attrs({NOTE: {"type": "Property", "value": "Not mine to write."}}, token=token_viewer)
    assert res.status_code in (401, 403), f"an ungranted caller wrote the note: {res.status_code}"
