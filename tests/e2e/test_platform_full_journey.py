"""The platform journey, one stage at a time (T-0331: PL-01, EP-01, EP-61, SP-09, AP-01).

One set of buses is followed from the feed that produced them to the map that draws them:

    HFP feed -> pipeline -> Vehicle entities in the `transport` space
             -> an Endpoint serving them as NGSI-LD and as GeoJSON
             -> an App declaring it needs exactly those representations
             -> a CKAN dataset whose resources are those representations
             -> a second space federating the first through a ContextSourceRegistration
             -> and a relationship linking every Vehicle back to what ingested it

Every stage is asserted twice, and the difference matters. The **shape** claims run against
committed fixtures with nothing configured, which is what keeps the fast lane non-empty and
what `selftest.py` breaks one stage at a time. The **journey** claims need a running platform
and skip cleanly without one, because a suite that passes because it was pointed at nothing is
worth nothing.

`tests/e2e` is this suite. The repository's `e2e/` at the root is the Playwright browser
journeys and is a different thing entirely.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

from journey import (
    entity_ids,
    feature_ids,
    get_json,
    leaked,
    misplaced_ids,
    relationship_targets,
    session,
    without_location,
)

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

# The CKAN client of `tests/ckan`, not a second one: this journey ends at the same catalogue
# that suite checks, and two clients would drift apart on the first CKAN quirk either meets.
sys.path.insert(0, str(HERE.parent / "ckan"))
from ckan_api import Ckan, CkanError  # noqa: E402

#: The organization and space the journey runs in (DEMO.md).
ORGANIZATION = os.environ.get("JOURNEY_ORG", "hel.fi")
SPACE = os.environ.get("JOURNEY_SPACE", "transport")
ENTITY_TYPE = os.environ.get("JOURNEY_TYPE", "Vehicle")
#: The HFP pipeline is capped at thirty buses (T-0295), so thirty is what a healthy run holds.
EXPECTED = int(os.environ.get("EXPECTED_VEHICLES", "30"))


def hidden() -> tuple[str, ...]:
    """Attribute names the Endpoint's projection removes (EP-61)."""
    declared = os.environ.get("HIDDEN_ATTRIBUTES", "")
    return tuple(name.strip() for name in declared.split(",") if name.strip())


def fixture(name: str) -> Any:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return yaml.safe_load(text) if name.endswith(".yaml") else json.loads(text)


# --- committed shapes: these run with nothing configured -------------------------------


@pytest.fixture(scope="module")
def committed_entities() -> list[dict[str, Any]]:
    return fixture("vehicles.jsonld")


@pytest.fixture(scope="module")
def committed_geojson() -> dict[str, Any]:
    return fixture("vehicles.geojson")


@pytest.fixture(scope="module")
def committed_app() -> dict[str, Any]:
    return fixture("app.yaml")


@pytest.fixture(scope="module")
def committed_package() -> dict[str, Any]:
    return fixture("ckan-package.json")


@pytest.fixture(scope="module")
def committed_csr() -> dict[str, Any]:
    return fixture("csr.jsonld")


# --- what a running platform answers ---------------------------------------------------


@pytest.fixture(scope="module")
def space() -> requests.Session:
    if not os.environ.get("SPACE_URL"):
        pytest.skip("SPACE_URL is not set")
    return session("SPACE_TOKEN")


@pytest.fixture(scope="module")
def served_entities(space: requests.Session) -> list[dict[str, Any]]:
    """Every entity of the journey's type in the space surface (SP-09)."""
    base = os.environ["SPACE_URL"].rstrip("/")
    body = get_json(
        space,
        f"{base}/entities",
        "application/ld+json",
        type=ENTITY_TYPE,
        limit=str(max(EXPECTED * 2, 100)),
    )
    assert isinstance(body, list), f"the space answered {type(body).__name__}, not a list"
    return body


@pytest.fixture(scope="module")
def endpoint() -> str:
    url = os.environ.get("ENDPOINT_URL")
    if not url:
        pytest.skip("ENDPOINT_URL is not set")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def gateway() -> requests.Session:
    return session("GATEWAY_TOKEN")


@pytest.fixture(scope="module")
def endpoint_entities(endpoint: str, gateway: requests.Session) -> list[dict[str, Any]]:
    return get_json(
        gateway, f"{endpoint}/ngsi-ld/v1/entities", "application/ld+json", type=ENTITY_TYPE
    )


@pytest.fixture(scope="module")
def endpoint_geojson(endpoint: str, gateway: requests.Session) -> dict[str, Any]:
    return get_json(gateway, f"{endpoint}/file.geojson", "application/geo+json")


# --- 1. the feed became entities (PL-01) -----------------------------------------------


def test_every_committed_entity_belongs_to_its_organization_and_space(
    committed_entities: list[dict[str, Any]]
) -> None:
    assert misplaced_ids(committed_entities, ORGANIZATION, SPACE) == []


def test_every_committed_entity_can_be_drawn_on_a_map(
    committed_entities: list[dict[str, Any]]
) -> None:
    assert without_location(committed_entities) == []


def test_pl01_the_pipeline_left_a_full_set_of_vehicles(
    served_entities: list[dict[str, Any]]
) -> None:
    """The HFP pipeline caps at thirty buses and keeps them fresh (T-0295). Fewer than that is
    a run that started and stopped, which looks identical to a healthy one in a counter."""
    assert len(served_entities) >= EXPECTED, f"{len(served_entities)} entities, expected {EXPECTED}"


def test_sp09_no_entity_in_the_space_was_minted_under_a_foreign_prefix(
    served_entities: list[dict[str, Any]]
) -> None:
    assert misplaced_ids(served_entities, ORGANIZATION, SPACE) == []


def test_every_served_entity_can_be_drawn_on_a_map(
    served_entities: list[dict[str, Any]]
) -> None:
    assert without_location(served_entities) == []


# --- 2. the Endpoint serves the same entities several ways (EP-01) ---------------------


def test_the_committed_representations_describe_the_same_entities(
    committed_entities: list[dict[str, Any]], committed_geojson: dict[str, Any]
) -> None:
    assert feature_ids(committed_geojson) == entity_ids(committed_entities)


def test_ep01_the_two_representations_of_the_endpoint_describe_the_same_entities(
    endpoint_entities: list[dict[str, Any]], endpoint_geojson: dict[str, Any]
) -> None:
    """One Endpoint, one policy, several encodings (EP-01). A GeoJSON that carries entities the
    NGSI-LD representation does not is a second answer to the same question."""
    assert feature_ids(endpoint_geojson) == entity_ids(endpoint_entities)


def test_the_endpoint_serves_what_the_space_holds(
    served_entities: list[dict[str, Any]], endpoint_entities: list[dict[str, Any]]
) -> None:
    """Narrowing is allowed and widening is not: an Endpoint may publish a subset of its space,
    never an entity the space does not hold."""
    assert entity_ids(endpoint_entities) <= entity_ids(served_entities)


# --- 3. nothing the Endpoint hides gets through (EP-61) --------------------------------


def test_ep61_no_hidden_attribute_is_in_any_representation(
    endpoint_entities: list[dict[str, Any]], endpoint_geojson: dict[str, Any]
) -> None:
    """Both encodings, because the projection is applied before the encoder and a leak in one
    of them is the projection running in the wrong place (EP-61)."""
    names = hidden()
    if not names:
        pytest.skip("HIDDEN_ATTRIBUTES is not set")
    assert leaked(endpoint_entities, names) == []
    assert leaked(endpoint_geojson, names) == []


def test_ep61_no_hidden_attribute_is_named_in_the_schema(
    endpoint: str, gateway: requests.Session
) -> None:
    """A schema that describes an attribute nobody is served still tells a reader it exists."""
    names = hidden()
    if not names:
        pytest.skip("HIDDEN_ATTRIBUTES is not set")
    schema = get_json(gateway, f"{endpoint}/schema/v1/json-schema", "application/schema+json")
    assert leaked(schema, names) == []


# --- 4. the app that renders them is declared (AP-01) ----------------------------------


def app_manifest() -> dict[str, Any]:
    path = os.environ.get("APP_MANIFEST")
    if not path:
        return fixture("app.yaml")
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_ap01_the_app_is_declared_with_everything_an_app_has_to_declare() -> None:
    manifest = app_manifest()
    assert manifest.get("kind") == "App", manifest.get("kind")
    spec = manifest.get("spec") or {}
    assert spec.get("kind") in {"static", "service", "fullstack"}, spec.get("kind")
    for field in ("source", "build", "visibility", "dataNeeds", "limits"):
        assert spec.get(field), f"an App with no spec.{field}"
    title = (manifest.get("metadata") or {}).get("title")
    assert isinstance(title, dict) and title, "metadata.title is a language map, not a string"


def test_ap01_the_app_asks_for_the_endpoint_the_journey_serves(
    committed_app: dict[str, Any]
) -> None:
    needs = committed_app["spec"]["dataNeeds"]
    assert needs, "an app that needs no data renders nothing"
    assert all(need.get("endpoint") for need in needs), needs
    assert all(need.get("representations") for need in needs), needs


# --- 5. the catalogue entry is those representations (T-0316) --------------------------


def representations(app: dict[str, Any]) -> set[str]:
    return {
        representation
        for need in app["spec"]["dataNeeds"]
        for representation in need.get("representations") or []
    }


def test_the_catalogue_entry_carries_every_representation_the_app_needs(
    committed_app: dict[str, Any], committed_package: dict[str, Any]
) -> None:
    """The publisher builds the dataset from the Endpoint's representations (T-0316), so a
    representation an app depends on and the catalogue does not carry is a broken harvest."""
    urls = " ".join(str(resource.get("url", "")) for resource in committed_package["resources"])
    for representation in representations(committed_app):
        assert representation in urls, f"no resource for {representation}: {urls}"


@pytest.fixture(scope="module")
def catalogue() -> Ckan:
    instance = Ckan.from_environment()
    if instance is None:
        pytest.skip("CKAN_URL is not set")
    return instance


@pytest.fixture(scope="module")
def dataset(catalogue: Ckan) -> dict[str, Any]:
    name = os.environ.get("CKAN_DATASET")
    if not name:
        pytest.skip("CKAN_DATASET is not set")
    try:
        return catalogue.package(name)
    except CkanError as error:
        pytest.fail(f"the journey left no dataset '{name}': {error}")


def test_every_resource_of_the_published_dataset_resolves(
    catalogue: Ckan, dataset: dict[str, Any]
) -> None:
    """Resolving is the claim, not being open: a restricted resource answering 401 is the
    policy working, a 404 is a resource pointing at nothing."""
    for resource in dataset.get("resources") or []:
        url = resource.get("url")
        assert url, f"a resource with no url: {resource.get('name')}"
        assert catalogue.reachable(url) != 404, url


# --- 6. a second space federates the first (SP-09) -------------------------------------


def test_the_committed_registration_names_a_source_and_what_it_holds(
    committed_csr: dict[str, Any]
) -> None:
    assert committed_csr.get("type") == "ContextSourceRegistration"
    assert committed_csr.get("endpoint"), "a registration with no endpoint federates nothing"
    information = committed_csr.get("information") or []
    types = {
        entry.get("type")
        for block in information
        for entry in block.get("entities") or []
    }
    assert ENTITY_TYPE in types, types


def test_sp09_the_federated_space_answers_with_the_entities_of_the_registered_one(
    served_entities: list[dict[str, Any]]
) -> None:
    """A ContextSourceRegistration makes another tenant's entities answerable here without
    copying them (T-0303). The ids stay the source's, which is how a reader can tell."""
    federated = os.environ.get("FEDERATED_SPACE_URL")
    if not federated:
        pytest.skip("FEDERATED_SPACE_URL is not set")
    body = get_json(
        session("SPACE_TOKEN"),
        f"{federated.rstrip('/')}/entities",
        "application/ld+json",
        type=ENTITY_TYPE,
        limit=str(max(EXPECTED * 2, 100)),
    )
    assert entity_ids(served_entities) <= entity_ids(body), "the federated space answered fewer"


# --- 7. the graph is linked ------------------------------------------------------------


def link_attribute() -> str:
    return os.environ.get("GRAPH_LINK_ATTRIBUTE", "refDataSource")


def test_every_committed_entity_points_back_at_what_ingested_it(
    committed_entities: list[dict[str, Any]]
) -> None:
    attribute = link_attribute()
    for entity in committed_entities:
        targets = relationship_targets(entity, attribute)
        assert targets, f"{entity['id']} carries no {attribute}"
        for target in targets:
            assert target.startswith("urn:ngsi-ld:"), target
            assert f":{ORGANIZATION}:" in target, f"{target} leaves the organization"


def test_every_served_entity_points_back_at_what_ingested_it(
    served_entities: list[dict[str, Any]]
) -> None:
    """The link is what turns thirty rows into a graph: without it a Vehicle says nothing about
    where it came from, and provenance stops at the pipeline's own logs."""
    attribute = link_attribute()
    unlinked = [
        str(entity.get("id"))
        for entity in served_entities
        if not relationship_targets(entity, attribute)
    ]
    assert unlinked == [], unlinked
