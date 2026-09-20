"""SensorThings API v1.1 Sensing Profile read path (T-0059, TS-08, EP-12, EP-13).

docs/Testing/02-conformance-tests.md §3 claims Core Sensing entities, $filter, $select and
$expand, and explicitly does not claim DataArray, MultiDatastream or Tasking. Every test is named
after the requirement it holds the endpoint to.
"""

from __future__ import annotations

import pytest

CLAIMED_SETS = ["Things", "Locations", "Datastreams", "Observations", "ObservedProperties", "Sensors"]
NOT_CLAIMED_SETS = ["MultiDatastreams", "Tasks", "TaskingCapabilities", "Actuators"]


def test_ts08_service_root_lists_the_claimed_entity_sets(get):
    """TS-08 — The sta/v1.1/ read-only representation MUST pass the OGC SensorThings API Sensing Profile conformance tests for all supported core collections."""
    response = get("")
    assert response.status_code == 200
    listed = {entry.get("name"): entry.get("url") for entry in response.json().get("value", [])}
    assert listed, "the service root advertises no entity set"
    for name in CLAIMED_SETS:
        assert name in listed, f"the Sensing Profile set {name} is not advertised: {sorted(listed)}"
        assert listed[name], f"{name} is advertised without a url"
    for name in NOT_CLAIMED_SETS:
        assert name not in listed, f"{name} is advertised but not claimed by the platform"


def test_ep12_things_carry_the_sta_shape(get):
    """EP-12 — SensorThings representations (sta/v1.1/) MUST map Smart Data Models observation entities directly to STA entity sets."""
    response = get("Things", _top=1)
    assert response.status_code == 200
    values = response.json().get("value", [])
    if not values:
        pytest.skip("no Thing to inspect")
    thing = values[0]
    for field in ("@iot.id", "name", "description"):
        assert field in thing, f"a Thing without {field} is not an STA Thing: {sorted(thing)}"
    assert "Datastreams@iot.navigationLink" in thing, "a Thing must navigate to its Datastreams"


def test_ep12_observations_carry_phenomenon_time_and_result(observations):
    """EP-12 — SensorThings representations (sta/v1.1/) MUST map Smart Data Models observation entities directly to STA entity sets."""
    for observation in observations:
        assert "phenomenonTime" in observation, f"Observation {observation.get('@iot.id')} has no phenomenonTime"
        assert "result" in observation, f"Observation {observation.get('@iot.id')} has no result"
        assert "Datastream@iot.navigationLink" in observation or "Datastream" in observation, (
            "an Observation must reference the Datastream it belongs to"
        )


def test_ts08_filter_narrows_and_its_negation_excludes(get, observations):
    """TS-08 — Observation filtering ($filter) is transpiled to NGSI-LD q and temporalQ queries."""
    observation = observations[0]
    identifier = observation["@iot.id"]
    literal = f"'{identifier}'" if isinstance(identifier, str) else str(identifier)

    matching = get("Observations", _filter=f"id eq {literal}")
    assert matching.status_code == 200, f"$filter answered {matching.status_code}"
    assert [o["@iot.id"] for o in matching.json()["value"]] == [identifier]

    excluding = get("Observations", _filter=f"id ne {literal}", _top=50)
    assert excluding.status_code == 200
    assert identifier not in [o["@iot.id"] for o in excluding.json()["value"]], (
        "the negation of a matching filter still returned the observation"
    )


def test_ts08_select_returns_only_the_requested_fields(get, observations):
    """TS-08 — Field projection ($select) is transpiled to the NGSI-LD attrs parameter."""
    response = get("Observations", _select="result", _top=1)
    assert response.status_code == 200
    value = response.json()["value"][0]
    assert "result" in value, "$select=result did not return result"
    assert "phenomenonTime" not in value, f"$select=result also returned {sorted(value)}"


def test_ts08_expand_embeds_the_related_entities(get):
    """TS-08 — Entity expansion ($expand) is supported for parent/child relations (Datastream/Observations)."""
    response = get("Datastreams", _expand="Observations($top=1)", _top=1)
    assert response.status_code == 200
    values = response.json().get("value", [])
    if not values:
        pytest.skip("no Datastream to expand")
    assert "Observations" in values[0], f"$expand did not embed Observations: {sorted(values[0])}"
    assert isinstance(values[0]["Observations"], list)


def test_ts08_pagination_uses_top_skip_and_next_link(get, http_session):
    """TS-08 — The Sensing Profile pagination contract: $top, $skip and @iot.nextLink."""
    first = get("Observations", _top=1)
    assert first.status_code == 200
    body = first.json()
    assert len(body["value"]) <= 1, "$top=1 returned more than one entity"

    next_link = body.get("@iot.nextLink")
    if not next_link:
        pytest.skip("a single page holds every Observation, so there is no nextLink")
    second = http_session.get(next_link, timeout=60)
    assert second.status_code == 200, f"@iot.nextLink answered {second.status_code}"
    first_ids = [o["@iot.id"] for o in body["value"]]
    second_ids = [o["@iot.id"] for o in second.json()["value"]]
    assert not set(first_ids) & set(second_ids), "the next page repeats entities already served"


def test_ep13_write_methods_are_rejected(http_session, sta_url):
    """EP-13 — Inexpressible STA entities MUST return HTTP 404 Not Found, while non-temporal write operations MUST be rejected with HTTP 405 Method Not Allowed."""
    for entity_set in ("Things", "Observations", "Datastreams"):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = http_session.request(
                method, f"{sta_url}/{entity_set}", json={"name": "conformance"}, timeout=60
            )
            assert response.status_code == 405, (
                f"{method} {entity_set} answered {response.status_code}, expected 405"
            )


def test_ep13_entity_sets_outside_the_profile_are_404(get):
    """EP-13 — Inexpressible STA entities MUST return HTTP 404 Not Found, while non-temporal write operations MUST be rejected with HTTP 405 Method Not Allowed."""
    for entity_set in NOT_CLAIMED_SETS:
        response = get(entity_set)
        assert response.status_code == 404, (
            f"{entity_set} answered {response.status_code}; an inexpressible entity set is a 404"
        )


def test_ep13_an_unknown_entity_is_404(get):
    """EP-13 — Inexpressible STA entities MUST return HTTP 404 Not Found, while non-temporal write operations MUST be rejected with HTTP 405 Method Not Allowed."""
    for path in ("Things('urn:ngsi-ld:Device:conformance:does-not-exist')", "Observations(999999999)"):
        response = get(path)
        assert response.status_code == 404, f"{path} answered {response.status_code}"
