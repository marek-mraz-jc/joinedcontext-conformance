"""What a view lets a client write, and what a write may link (surfaces-conformance.md section 4).

- `test_capabilities_annotations_are_obeyed`: every write a `Capabilities` restriction of
  `$metadata` forbids is refused with the OData error object and writes nothing (view
  `parity-restricted` offers no write);
- `test_expand_skips_out_of_view_targets`: `$expand` of a Relationship whose target the view hides
  (ADR-0023 rule 6) reads like a dangling link, while view `all` expands it;
- `test_deep_insert_and_ref_check_the_linked_entity`: a deep insert is 501, a bind to a target the
  view does not see is 400 like an absent one (the bind is the request body's, Protocol 11.4.2.1,
  Antares T-3760), a `$ref` to it is 404, and nothing is written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from conftest import ENTITY_SET, count, is_odata_error

EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
BB1 = "urn:ngsi-ld:AirQualityObserved:bb-1"
BB2 = "urn:ngsi-ld:AirQualityObserved:bb-2"
ABSENT = "urn:ngsi-ld:AirQualityObserved:absent"


def restrictions(http, service_root: str) -> dict[str, bool]:
    """`Insertable`, `Updatable`, `Deletable` of the entity set, as `$metadata` writes them."""
    resp = http.get(f"{service_root}/$metadata", headers={"Accept": "application/xml"})
    assert resp.status_code == 200, resp.text
    entity_set = next(
        s for s in ET.fromstring(resp.content).iter(f"{EDM}EntitySet") if s.get("Name") == ENTITY_SET
    )
    flags = {}
    for value in entity_set.iter(f"{EDM}PropertyValue"):
        if value.get("Property") in ("Insertable", "Updatable", "Deletable"):
            flags[value.get("Property")] = value.get("Bool") == "true"
    return flags


def test_capabilities_annotations_are_obeyed(http, odata_url, restricted_url):
    assert restrictions(http, odata_url) == {"Insertable": True, "Updatable": True, "Deletable": True}
    flags = restrictions(http, restricted_url)
    assert flags == {"Insertable": False, "Updatable": False, "Deletable": False}, flags
    one = f"{restricted_url}/{ENTITY_SET}('{BB1}')"
    before = count(http, odata_url)
    for flag, method, url, body in [
        ("Insertable", "POST", f"{restricted_url}/{ENTITY_SET}", {"id": "urn:ngsi-ld:AirQualityObserved:new"}),
        ("Updatable", "PATCH", one, {"temperature": 1.0}),
        ("Updatable", "PUT", one, {"temperature": 1.0}),
        ("Deletable", "DELETE", one, None),
    ]:
        resp = http.request(method, url, json=body)
        assert resp.status_code == 405, f"{flag} {method}: {resp.status_code} {resp.text}"
        assert is_odata_error(resp), resp.text
    assert count(http, odata_url) == before
    stored = http.get(f"{odata_url}/{ENTITY_SET}('{BB1}')").json()
    assert stored["temperature"] == 11.5, stored


def test_expand_skips_out_of_view_targets(http, odata_url, restricted_url):
    seen = http.get(f"{odata_url}/{ENTITY_SET}('{BB1}')?$expand=nearest&$select=id")
    assert seen.status_code == 200, seen.text
    assert seen.json()["nearest"]["id"] == "urn:ngsi-ld:AirQualityObserved:ba-1", seen.text
    hidden = http.get(f"{restricted_url}/{ENTITY_SET}('{BB1}')?$expand=nearest&$select=id")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["nearest"] is None, hidden.text


def test_deep_insert_and_ref_check_the_linked_entity(http, odata_url):
    before = count(http, odata_url)
    new = "urn:ngsi-ld:AirQualityObserved:linked"
    for body, status in [
        ({"id": new, "nearest": {"id": ABSENT, "temperature": 1.0}}, 501),
        ({"id": new, "nearest@odata.bind": f"{ENTITY_SET}('{ABSENT}')"}, 400),
    ]:
        resp = http.post(f"{odata_url}/{ENTITY_SET}", json=body)
        assert resp.status_code == status, f"{body}: {resp.status_code} {resp.text}"
        assert is_odata_error(resp), resp.text
    ref = {"@odata.id": f"{ENTITY_SET}('{ABSENT}')"}
    resp = http.put(f"{odata_url}/{ENTITY_SET}('{BB2}')/nearest/$ref", json=ref)
    assert resp.status_code == 404, resp.text
    assert is_odata_error(resp), resp.text
    assert count(http, odata_url) == before
    assert http.get(f"{odata_url}/{ENTITY_SET}('{BB2}')?$expand=nearest").json()["nearest"] is None
