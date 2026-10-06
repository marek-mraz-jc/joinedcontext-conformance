"""The DataStore completeness check itself (T-3112…T-3120): offline, so the fast lane holds it,
and live per dataset when `CKAN_URL` and `CKAN_DATASETS` name them."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from completeness import Reader, cell_of, check_dataset, compare, missing_attributes, same, stem

ENTITY = {
    "id": "urn:ngsi-ld:Vehicle:hel.fi:helsinki:12-2201",
    "type": "Vehicle",
    "speed": {"type": "Property", "value": 7.5, "observedAt": "2026-10-06T17:00:00Z", "unitCode": "MTS"},
    "name": {"type": "LanguageProperty", "languageMap": {"fi": "Linja 12", "en": "Line 12"}},
    "refRoute": {"type": "Relationship", "object": "urn:ngsi-ld:Route:hel.fi:helsinki:12"},
    "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [24.95, 60.17]}},
}


def test_a_column_carries_the_attribute_before_its_first_dot_or_bracket():
    assert stem("location.value.coordinates[0]") == "location"
    assert stem("name.languageMap.fi") == "name"
    assert stem("entity_id") == "entity_id"


def test_an_attribute_without_a_column_is_named_and_id_and_type_never_are():
    properties = {"id": {}, "type": {}, "speed": {}, "name": {}, "refRoute": {}}
    assert missing_attributes(["speed.value", "name.languageMap.fi"], properties) == ["refRoute"]
    assert missing_attributes(["speed.value", "name.languageMap.en", "refRoute.object"], properties) == []


def test_each_kind_of_column_reads_its_value_from_the_entity():
    assert cell_of(ENTITY, "speed.value") == (True, 7.5)
    assert cell_of(ENTITY, "speed.unitCode") == (True, "MTS")
    assert cell_of(ENTITY, "name.languageMap.en") == (True, "Line 12")
    assert cell_of(ENTITY, "refRoute.object") == (True, "urn:ngsi-ld:Route:hel.fi:helsinki:12")
    assert cell_of(ENTITY, "location.value.coordinates[1]") == (True, 60.17)
    assert cell_of(ENTITY, "location.geojson")[1] == {"type": "Point", "coordinates": [24.95, 60.17]}
    assert cell_of(ENTITY, "status.value") == (False, None)


def test_a_cell_equals_the_broker_as_ckan_types_it():
    assert same(7, 7.0)
    assert same('{"type":"Point","coordinates":[24.95,60.17]}', ENTITY["location"]["value"])
    assert same("2026-10-06T17:00:00+00:00", "2026-10-06T17:00:00Z")
    assert not same(7.6, 7.5)
    assert not same("Linja 13", "Linja 12")


def test_a_row_the_broker_has_observed_again_moved_on_and_a_wrong_one_is_named():
    row = {"_id": 1, "entity_id": ENTITY["id"], "type": "Vehicle", "speed.value": 3.0, "speed.observedAt": "2026-10-06T16:00:00Z"}
    wrong, moved = compare(row, ENTITY)
    assert (wrong, moved) == ([], 2), "the value and its timestamp are both newer in the broker"
    stale = {"_id": 1, "entity_id": ENTITY["id"], "speed.value": 3.0, "speed.observedAt": "2026-10-06T17:00:00Z"}
    wrong, moved = compare(stale, ENTITY)
    assert moved == 0 and wrong == ["speed.value: table 3.0, broker 7.5"]
    ghost = {"_id": 1, "entity_id": ENTITY["id"], "status.value": "open"}
    assert compare(ghost, ENTITY)[0] == ["status.value: the broker has none, the table 'open'"]


DATASETS = [name for name in os.environ.get("CKAN_DATASETS", "").split(",") if name]


@pytest.mark.skipif(not (os.environ.get("CKAN_URL") and DATASETS), reason="CKAN_URL and CKAN_DATASETS name no live catalogue")
@pytest.mark.parametrize("dataset", DATASETS)
def test_every_datastore_table_of_the_dataset_equals_its_endpoint(dataset):
    hours = float(os.environ.get("CKAN_MAX_AGE_HOURS", "24"))
    report = check_dataset(Reader(os.environ["CKAN_URL"], os.environ.get("JC_GATEWAY_TOKEN")), dataset, timedelta(hours=hours))
    failed = [f"{check.name}: {check.detail}" for check in report.checks if not check.ok]
    assert not failed, "\n".join(failed)
