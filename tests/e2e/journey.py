"""The shapes the platform journey is asserted against (T-0331).

Only pure functions and one reader, so both `test_platform_full_journey.py` and `selftest.py`
make the same claims about the same bytes: a selftest that re-implements the rule it is proving
proves nothing.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

import requests

TIMEOUT = 30

#: `urn:ngsi-ld:{Type}:{orgDomain}:{space}:{localId}` — the id shape every entity of this
#: platform carries (DEMO.md, docs Architecture/03). The local id is whatever the pipeline
#: minted and may itself contain colons, so it is the remainder and not one segment.
URN = re.compile(r"^urn:ngsi-ld:(?P<type>[^:]+):(?P<org>[^:]+):(?P<space>[^:]+):(?P<local>.+)$")

#: NGSI-LD keys that are not attributes, so a hidden-attribute check does not trip over them.
RESERVED = frozenset({"id", "type", "@context", "createdAt", "modifiedAt", "observedAt"})


def session(token_variable: str) -> requests.Session:
    """A session carrying the bearer token that variable names, if it names one."""
    made = requests.Session()
    token = os.environ.get(token_variable)
    if token:
        made.headers["Authorization"] = f"Bearer {token}"
    return made


def get_json(client: requests.Session, url: str, accept: str, **params: Any) -> Any:
    answer = client.get(url, headers={"Accept": accept}, params=params, timeout=TIMEOUT)
    assert answer.status_code == 200, f"{url} answered {answer.status_code}"
    return answer.json()


def entity_ids(entities: Iterable[dict[str, Any]]) -> set[str]:
    return {str(entity["id"]) for entity in entities if entity.get("id")}


def feature_ids(collection: dict[str, Any]) -> set[str]:
    """The entity ids a GeoJSON representation carries.

    NGSI-LD puts the entity id in the feature's `id`; a producer that only writes it into
    `properties` is still readable, and reading both is what makes the comparison with the
    NGSI-LD representation an equality rather than a coincidence.
    """
    found = set()
    for feature in collection.get("features") or []:
        identifier = feature.get("id") or (feature.get("properties") or {}).get("id")
        if identifier:
            found.add(str(identifier))
    return found


def misplaced_ids(entities: Iterable[dict[str, Any]], organization: str, space: str) -> list[str]:
    """Ids that do not belong to this organization and space (SP-09).

    An id minted under someone else's prefix in a tenant surface is the tenancy failing, which
    is why this is checked on every entity and not on a sample.
    """
    wrong = []
    for entity in entities:
        match = URN.match(str(entity.get("id", "")))
        if not match or match["org"] != organization or match["space"] != space:
            wrong.append(str(entity.get("id")))
    return wrong


def without_location(entities: Iterable[dict[str, Any]]) -> list[str]:
    """Entities a map cannot draw: no `location`, or one that is not a GeoJSON geometry."""
    missing = []
    for entity in entities:
        location = entity.get("location")
        value = location.get("value") if isinstance(location, dict) else location
        if not isinstance(value, dict) or not value.get("type") or value.get("coordinates") is None:
            missing.append(str(entity.get("id")))
    return missing


def leaked(payload: Any, hidden: Iterable[str]) -> list[str]:
    """Hidden attribute names that appear anywhere in a payload (EP-61).

    As a key at any depth, because an empty attribute still tells a reader it exists, and in a
    GeoJSON `properties` block just as much as in an NGSI-LD body.
    """
    names = {name for name in hidden if name}
    if not names:
        return []
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in names:
                    found.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(found)


def relationship_targets(entity: dict[str, Any], attribute: str) -> list[str]:
    """The entity ids one Relationship attribute points at, in either NGSI-LD form.

    Normalized form is `{"type": "Relationship", "object": "<urn>"}`; a multi-attribute is a
    list of those. A key-value representation carries the urn directly.
    """
    node = entity.get(attribute)
    if node is None:
        return []
    entries = node if isinstance(node, list) else [node]
    targets = []
    for entry in entries:
        if isinstance(entry, dict):
            target = entry.get("object")
        else:
            target = entry
        if target:
            targets.append(str(target))
    return targets
