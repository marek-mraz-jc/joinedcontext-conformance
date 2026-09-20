"""DCAT-AP 3.0 conformance and CKAN publication, end to end (T-0320, EP-27, EP-61…EP-67).

Three claims, and each one is checked where it can actually be checked.

The record is DCAT-AP. That holds for the committed fixture with nothing configured, and for
whatever a live endpoint answers when `ENDPOINT_URL` names one. The catalogue entry is built
from that record and nothing else (EP-63), so a record that conforms is the entry conforming.

The publication happened. That needs a CKAN with a dataset in it, so it runs when `CKAN_URL`
and `CKAN_DATASET` name one: the dataset exists, its resources point at URLs that resolve,
and the DataStore table behind it holds rows.

Nothing masked got through. That is the reason the publisher is an ordinary consumer of the
Endpoint (EP-66), and it is asserted on both outputs at once: name the attributes the
Endpoint's policy hides in `MASKED_ATTRIBUTES` and neither the record nor a DataStore row may
mention one.

Skipping is deliberate and per claim. A suite that passes because it was pointed at nothing
is worth nothing, so the shape checks always run, and `run.sh` reports the rest as skipped
rather than as passed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import requests

from ckan_api import Ckan, CkanError, datastore_resource
from dcat import (
    DATASET_TYPE,
    MANDATORY_DATASET_TERMS,
    distributions,
    leaked,
    missing_terms,
    shape_violations,
)

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TIMEOUT = 30

#: The shapes the fast lane validates against, and the ones an operator points at the
#: official SEMIC distribution with.
CORE_SHAPES = (FIXTURES / "dcat-ap-3.0.core.shapes.ttl").read_text(encoding="utf-8")


def official_shapes() -> str | None:
    """The DCAT-AP 3.0 shapes `DCAT_AP_SHACL` names, or None where it names none."""
    path = os.environ.get("DCAT_AP_SHACL")
    if not path:
        return None
    resolved = Path(path)
    if resolved.is_dir():
        return "\n".join(
            file.read_text(encoding="utf-8") for file in sorted(resolved.glob("*.ttl"))
        )
    return resolved.read_text(encoding="utf-8")


def masked() -> tuple[str, ...]:
    """Attribute names the Endpoint's policy set hides (EP-61)."""
    declared = os.environ.get("MASKED_ATTRIBUTES", "")
    return tuple(name.strip() for name in declared.split(",") if name.strip())


@pytest.fixture(scope="module")
def fixture_record() -> dict[str, Any]:
    """A record shaped the way the gateway answers, committed so the lane is never empty."""
    return json.loads((FIXTURES / "endpoint-record.jsonld").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def served_record() -> dict[str, Any]:
    """What a live endpoint answers at its root (EP-27)."""
    url = os.environ.get("ENDPOINT_URL")
    if not url:
        pytest.skip("ENDPOINT_URL is not set")
    session = requests.Session()
    token = os.environ.get("GATEWAY_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    answer = session.get(
        f"{url.rstrip('/')}/", headers={"Accept": "application/ld+json"}, timeout=TIMEOUT
    )
    assert answer.status_code == 200, f"the endpoint root answered {answer.status_code}"
    return answer.json()


@pytest.fixture(scope="module")
def ckan() -> Ckan:
    instance = Ckan.from_environment()
    if instance is None:
        pytest.skip("CKAN_URL is not set")
    return instance


@pytest.fixture(scope="module")
def dataset(ckan: Ckan) -> dict[str, Any]:
    name = os.environ.get("CKAN_DATASET")
    if not name:
        pytest.skip("CKAN_DATASET is not set")
    try:
        return ckan.package(name)
    except CkanError as error:
        pytest.fail(f"the publication left no dataset '{name}': {error}")


# --- the record is DCAT-AP ------------------------------------------------------------


def test_ep27_the_committed_record_carries_every_mandatory_term(
    fixture_record: dict[str, Any]
) -> None:
    assert fixture_record.get("@type") == DATASET_TYPE
    assert missing_terms(fixture_record) == []


def test_ep27_the_committed_record_conforms_to_the_core_shapes(
    fixture_record: dict[str, Any]
) -> None:
    assert shape_violations(fixture_record, CORE_SHAPES) == []


def test_ep69_the_record_says_whether_the_data_behind_it_is_open(
    fixture_record: dict[str, Any]
) -> None:
    rights = str(fixture_record.get("dct:accessRights", ""))
    # EP-69: from the EU authority list, so a harvester that never logs in can read it.
    assert rights.endswith(("PUBLIC", "RESTRICTED")), rights


def test_ep68_every_distribution_names_where_it_is_and_what_it_is(
    fixture_record: dict[str, Any]
) -> None:
    published = distributions(fixture_record)
    assert published, "a dataset with no distribution publishes nothing"
    for distribution in published:
        url = distribution.get("dcat:accessURL")
        assert url, f"a distribution with no accessURL: {distribution}"
        assert distribution.get("dcat:mediaType"), f"{url} names no media type"


def test_ep27_the_served_record_carries_every_mandatory_term(
    served_record: dict[str, Any]
) -> None:
    assert served_record.get("@type") == DATASET_TYPE, served_record.get("@type")
    assert missing_terms(served_record) == [], sorted(MANDATORY_DATASET_TERMS)


def test_ep27_the_served_record_conforms_to_the_core_shapes(
    served_record: dict[str, Any]
) -> None:
    assert shape_violations(served_record, CORE_SHAPES) == []


def test_ep27_the_served_record_conforms_to_the_official_dcat_ap_shapes(
    served_record: dict[str, Any]
) -> None:
    shapes = official_shapes()
    if shapes is None:
        pytest.skip("DCAT_AP_SHACL does not name the DCAT-AP 3.0 shapes")
    assert shape_violations(served_record, shapes) == []


def test_the_fixture_conforms_to_the_official_dcat_ap_shapes(
    fixture_record: dict[str, Any]
) -> None:
    """The fixture is what the core shapes are calibrated against, so it has to survive the
    real ones too: a fixture that only the shapes in this repository accept would make every
    other assertion here weaker than it looks."""
    shapes = official_shapes()
    if shapes is None:
        pytest.skip("DCAT_AP_SHACL does not name the DCAT-AP 3.0 shapes")
    assert shape_violations(fixture_record, shapes) == []


# --- the publication happened ----------------------------------------------------------


def test_ep62_the_endpoint_became_exactly_one_dataset(dataset: dict[str, Any]) -> None:
    assert dataset.get("name"), dataset
    assert dataset.get("resources"), "the dataset carries no resource"


def test_ep63_the_dataset_metadata_is_the_records_own(
    dataset: dict[str, Any], served_record: dict[str, Any]
) -> None:
    """EP-63: the catalogue entry and the endpoint's description cannot disagree, because
    the entry is built from the record and nothing is authored a second time."""
    identifier = served_record.get("dct:identifier")
    extras = {extra["key"]: extra["value"] for extra in dataset.get("extras") or []}
    if identifier:
        assert extras.get("identifier") == identifier, extras
    assert extras.get("endpoint"), "the dataset does not say which endpoint it came from"


def test_ep64_every_resource_url_resolves(ckan: Ckan, dataset: dict[str, Any]) -> None:
    """A resource URL is that representation's own URL under the endpoint, so it passes the
    gateway. Accessible means it resolves: a restricted endpoint answering 401 is the policy
    working, and a 404 or a connection failure is a resource pointing at nothing."""
    for resource in dataset["resources"]:
        url = resource.get("url")
        assert url, f"a resource with no URL: {resource.get('name')}"
        status = ckan.reachable(url)
        assert status not in (404, 410), f"{url} answered {status}"
        assert status < 500, f"{url} answered {status}"


def test_ep65_the_datastore_table_holds_rows(ckan: Ckan, dataset: dict[str, Any]) -> None:
    resource = datastore_resource(dataset)
    if resource is None:
        pytest.skip("the dataset declares no DataStore mirror")
    rows = ckan.rows(resource["id"])
    assert rows, "the DataStore table behind the dataset is empty"
    # EP-65: the mirror is keyed by the entity, which is what makes a refresh an upsert
    # rather than an append.
    assert all(row.get("entity_id") for row in rows), rows[0]
    ids = [row["entity_id"] for row in rows]
    assert len(ids) == len(set(ids)), "the same entity is in the table twice"


# --- nothing masked got through ---------------------------------------------------------


def test_ep61_ep66_no_masked_attribute_reaches_the_record(
    served_record: dict[str, Any]
) -> None:
    hidden = masked()
    if not hidden:
        pytest.skip("MASKED_ATTRIBUTES names no attribute the endpoint hides")
    assert leaked(served_record, hidden) == []


def test_ep61_ep66_no_masked_attribute_reaches_the_datastore(
    ckan: Ckan, dataset: dict[str, Any]
) -> None:
    hidden = masked()
    if not hidden:
        pytest.skip("MASKED_ATTRIBUTES names no attribute the endpoint hides")
    assert leaked(dataset, hidden) == [], "a masked attribute is in the dataset metadata"
    resource = datastore_resource(dataset)
    if resource is None:
        pytest.skip("the dataset declares no DataStore mirror")
    # The column names matter as much as the values: an empty column named after a masked
    # attribute still tells a reader the attribute exists.
    assert leaked(ckan.rows(resource["id"]), hidden) == []
