"""What an Endpoint publishes about its own data, checked over HTTP (T-0337, EP-27, EP-46, EP-68).

The suite in `test_linkml_artifacts.py` reads the checkout: it proves that what Model Tools
generated is what was committed. This one reads the endpoint: it proves that what a consumer
downloads is coherent with itself. Those are different claims, and only the second one covers
the projection, because the endpoint narrows every artifact to the caller's grant and the
checkout knows nothing about grants.

Skipped whole unless `ENDPOINT_URL` names a running endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import pytest
import requests

from artifacts import shacl_violations_of

TIMEOUT = 30


def _endpoint_url() -> str:
    url = os.environ.get("ENDPOINT_URL")
    if not url:
        pytest.skip("ENDPOINT_URL is not set")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def session() -> requests.Session:
    s = requests.Session()
    token = os.environ.get("GATEWAY_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def base(session: requests.Session) -> str:
    return _endpoint_url()


@pytest.fixture(scope="module")
def record(session: requests.Session, base: str) -> dict[str, Any]:
    """The endpoint's own DCAT-AP record (EP-27)."""
    answer = session.get(f"{base}/", headers={"Accept": "application/ld+json"}, timeout=TIMEOUT)
    assert answer.status_code == 200, f"GET {base}/ answered {answer.status_code}"
    return answer.json()


def _distributions(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in record.get("dcat:distribution", []) if isinstance(d, dict)]


def _schema_distributions(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        d
        for d in _distributions(record)
        if "/schema/v" in str(d.get("dcat:accessURL", ""))
    ]


def test_ep27_the_endpoint_root_answers_a_dcat_ap_record(record: dict[str, Any]) -> None:
    assert record.get("@type") == "dcat:Dataset", record
    assert record.get("dct:identifier"), "the record names no identifier"
    assert _distributions(record), "the record lists no distribution at all"


def test_ep69_the_record_declares_its_access_rights(record: dict[str, Any]) -> None:
    rights = str(record.get("dct:accessRights", ""))
    assert rights.endswith("/PUBLIC") or rights.endswith("/RESTRICTED"), rights
    if rights.endswith("/RESTRICTED"):
        # A connector dereferences this to build an offer (DS-08); a licensed endpoint lists
        # its offer beside it (EP-79).
        policies = record.get("odrl:hasPolicy", "")
        policies = policies if isinstance(policies, list) else [policies]
        assert any(str(p).endswith("/access") for p in policies if isinstance(p, str)), record


def test_ep68_every_schema_artifact_is_listed_with_its_formalism(record: dict[str, Any]) -> None:
    schema = _schema_distributions(record)
    assert schema, "the record lists no schema artifact, so a harvester cannot find the model"
    for distribution in schema:
        url = distribution["dcat:accessURL"]
        assert distribution.get("dcat:mediaType"), f"{url} names no media type"
        assert distribution.get("dct:conformsTo"), f"{url} names no formalism"
        checksum = distribution.get("spdx:checksum") or {}
        assert len(str(checksum.get("spdx:checksumValue", ""))) == 64, f"{url} carries no sha256"


def test_ep68_each_artifact_answers_with_the_declared_type_and_digest(
    session: requests.Session, record: dict[str, Any]
) -> None:
    """The digest in the record is of the projected document, so it must be the one served.

    A harvester that stored the record decides from this number whether the copy it holds is
    still current; a number that does not match the bytes makes every such decision wrong.
    """
    problems: list[str] = []
    for distribution in _schema_distributions(record):
        url = distribution["dcat:accessURL"]
        # The record names the IANA IRI of the media type (EP-78); the header names the type.
        declared_type = str(distribution.get("dcat:mediaType", "")).removeprefix(
            "https://www.iana.org/assignments/media-types/"
        )
        declared_sha = str(distribution.get("spdx:checksum", {}).get("spdx:checksumValue", ""))

        answer = session.get(url, timeout=TIMEOUT)
        if answer.status_code != 200:
            problems.append(f"{url} answered {answer.status_code}")
            continue
        served_type = answer.headers.get("Content-Type", "").split(";")[0].strip()
        if declared_type.split(";")[0].strip() != served_type:
            problems.append(f"{url} is {served_type}, the record says {declared_type}")
        served_sha = hashlib.sha256(answer.content).hexdigest()
        if served_sha != declared_sha:
            problems.append(f"{url} hashes to {served_sha}, the record says {declared_sha}")
    assert not problems, "\n".join(problems)


def _artifact(record: dict[str, Any], suffix: str) -> str | None:
    for distribution in _schema_distributions(record):
        url = str(distribution["dcat:accessURL"])
        if url.endswith(suffix):
            return url
    return None


@pytest.fixture(scope="module")
def entity(session: requests.Session, base: str, record: dict[str, Any]) -> dict[str, Any]:
    """One entity the endpoint actually serves, discovered through its own type list."""
    types = session.get(
        f"{base}/ngsi-ld/v1/types",
        headers={"Accept": "application/ld+json"},
        timeout=TIMEOUT,
    )
    if types.status_code != 200:
        pytest.skip(f"the endpoint's type list answered {types.status_code}")
    type_list = types.json().get("typeList") or []
    if not type_list:
        pytest.skip("the endpoint publishes no entity type")
    wanted = type_list[0] if isinstance(type_list[0], str) else type_list[0].get("id")

    answer = session.get(
        f"{base}/ngsi-ld/v1/entities",
        params={"type": wanted, "limit": 1},
        headers={"Accept": "application/ld+json"},
        timeout=TIMEOUT,
    )
    if answer.status_code != 200:
        pytest.skip(f"the endpoint's entity query answered {answer.status_code}")
    entities = answer.json()
    if not entities:
        pytest.skip(f"the endpoint serves no entity of type {wanted}")
    return entities[0]


def test_dm46_pyshacl_accepts_an_entity_the_endpoint_serves(
    session: requests.Session, record: dict[str, Any], entity: dict[str, Any]
) -> None:
    """The served shapes must accept the served data, or one of the two is wrong (EP-47)."""
    shapes_url = _artifact(record, "model.shacl.ttl")
    context_url = _artifact(record, "context.jsonld")
    if not shapes_url or not context_url:
        pytest.skip("the endpoint publishes no SHACL shapes or no @context")

    shapes = session.get(shapes_url, timeout=TIMEOUT)
    context = session.get(context_url, timeout=TIMEOUT)
    assert shapes.status_code == 200, shapes_url
    assert context.status_code == 200, context_url

    violations = shacl_violations_of(entity, context.json(), shapes.text)
    assert not violations, (
        f"the shapes at {shapes_url} reject an entity the same endpoint served:\n"
        + "\n".join(violations)
    )


def test_dm46_the_served_shapes_reject_an_entity_that_breaks_the_model(
    session: requests.Session, record: dict[str, Any], entity: dict[str, Any]
) -> None:
    """Shapes that accept anything prove nothing, so a broken entity has to be refused."""
    shapes_url = _artifact(record, "model.shacl.ttl")
    context_url = _artifact(record, "context.jsonld")
    if not shapes_url or not context_url:
        pytest.skip("the endpoint publishes no SHACL shapes or no @context")

    shapes = session.get(shapes_url, timeout=TIMEOUT)
    context = session.get(context_url, timeout=TIMEOUT)
    broken = broken_entity(entity)
    if broken is None:
        pytest.skip("the served entity carries nothing a shape constrains")

    violations = shacl_violations_of(broken, context.json(), shapes.text)
    assert violations, (
        f"the shapes at {shapes_url} accepted {json.dumps(broken)[:400]}, which breaks the model"
    )


def broken_entity(entity: dict[str, Any]) -> dict[str, Any] | None:
    """The same entity with one attribute made invalid, or `None` when there is none to break.

    A numeric Property with a string value is the break that every generated shape catches:
    `sh:datatype xsd:double` on a slot the model declares. Dropping a required slot would be
    the other candidate, and it is the wrong one here, because a projected shape legitimately
    omits `sh:minCount` for a slot the caller may not read.
    """
    for name, value in entity.items():
        if name in ("id", "type", "@context"):
            continue
        if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
            broken = json.loads(json.dumps(entity))
            broken[name]["value"] = "not a number"
            return broken
    return None
