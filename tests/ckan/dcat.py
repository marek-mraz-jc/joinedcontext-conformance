"""DCAT-AP 3.0 checks over what an Endpoint publishes (T-0320, EP-27, EP-61, EP-68, EP-69).

The record an Endpoint answers with is the catalogue entry: CKAN's dataset is built from it
and nothing else (EP-63), so a record that is not DCAT-AP is a catalogue entry that is not
DCAT-AP, and there is one place to check it. These are pure functions over documents already
in memory, because the same three checks run against a live endpoint, against a fixture and
against the self-test's deliberately broken records.

A record whose `@context` is an object declares its own prefixes and IRI coercions (EP-78), and
that context is used as it is, over the prefix map below. A record that names the remote
`https://www.w3.org/ns/dcat.jsonld` instead gets the prefix map in its place: a conformance run
that reaches the open internet to parse its own input passes or fails on somebody else's
uptime, and every term such a record uses is prefixed anyway.
"""

from __future__ import annotations

import json
from typing import Any

import rdflib
from pyshacl import validate

#: The namespaces a DCAT-AP record of this platform uses. Enough to expand every term the
#: Endpoint emits; a record using one that is missing expands it to nothing and fails the
#: mandatory-term check rather than passing quietly.
PREFIXES: dict[str, str] = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "adms": "http://www.w3.org/ns/adms#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "spdx": "http://spdx.org/rdf/terms#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}

#: What DCAT-AP 3.0 makes mandatory on a Dataset, and what the platform's own requirements
#: add to it: the access rights of EP-69, which is what tells a harvester that never logs in
#: whether the data behind the entry is open.
MANDATORY_DATASET_TERMS = ("dct:title", "dct:description", "dct:accessRights")

#: What DCAT-AP 3.0 makes mandatory on a Distribution.
MANDATORY_DISTRIBUTION_TERMS = ("dcat:accessURL",)

DATASET_TYPE = "dcat:Dataset"


def graph(record: dict[str, Any]) -> rdflib.Graph:
    """The record as RDF, expanded with the local prefix map rather than a fetched context."""
    payload = {key: value for key, value in record.items() if key != "@context"}
    own = record.get("@context")
    payload["@context"] = {**PREFIXES, **own} if isinstance(own, dict) else dict(PREFIXES)
    parsed = rdflib.Graph()
    parsed.parse(data=json.dumps(payload), format="json-ld")
    return parsed


def missing_terms(record: dict[str, Any]) -> list[str]:
    """The mandatory terms the record leaves out, dataset and distributions together."""
    missing = [term for term in MANDATORY_DATASET_TERMS if _absent(record, term)]
    for index, distribution in enumerate(distributions(record)):
        missing.extend(
            f"dcat:distribution[{index}].{term}"
            for term in MANDATORY_DISTRIBUTION_TERMS
            if _absent(distribution, term)
        )
    return missing


def distributions(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The record's distributions, however many it carries."""
    declared = record.get("dcat:distribution")
    if isinstance(declared, dict):
        return [declared]
    return [item for item in declared or [] if isinstance(item, dict)]


def shape_violations(record: dict[str, Any], shapes_ttl: str) -> list[str]:
    """Every SHACL violation the shapes report against the record (EP-27).

    A validation that selected no node conforms trivially, which is the one way this check
    can lie, so a record the shapes target nothing in is reported as a violation of its own.
    """
    try:
        data = graph(record)
    except Exception as error:  # noqa: BLE001 - a record that will not parse is the finding
        return [f"the record does not expand to RDF: {error}"]
    shapes = rdflib.Graph()
    try:
        shapes.parse(data=shapes_ttl, format="turtle")
    except Exception as error:  # noqa: BLE001
        return [f"the shapes graph does not parse: {error}"]

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    targets = set(shapes.objects(None, sh.targetClass))
    if targets:
        typed = set(data.objects(None, rdflib.RDF.type))
        if not targets & typed:
            return [
                "the record expands to no node of any sh:targetClass, so the shapes validated "
                f"nothing: shapes target {sorted(str(t) for t in targets)}, the record expanded "
                f"to {sorted(str(t) for t in typed) or '[]'}"
            ]

    conforms, _, text = validate(
        data_graph=data,
        shacl_graph=shapes,
        advanced=True,
        inference="none",
        allow_warnings=False,
    )
    if conforms:
        return []
    return [line.strip() for line in str(text).splitlines() if "Message:" in line] or [str(text)]


def leaked(document: Any, hidden: tuple[str, ...] | list[str]) -> list[str]:
    """The hidden attribute names that appear anywhere in a document (EP-61, EP-66).

    A masked attribute must be absent from the catalogue, not present and empty: the whole
    point of the publisher being an ordinary consumer is that what the policy removed never
    reaches CKAN at all. Keys and string values are both searched, because an attribute can
    leak as a column name, as a `dcterms:conformsTo` entry or inside a free-text description.
    """
    found: set[str] = set()
    _walk(document, tuple(hidden), found)
    return sorted(found)


def _walk(node: Any, hidden: tuple[str, ...], found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            for name in hidden:
                if name == key or key.endswith(f".{name}") or key.startswith(f"{name}."):
                    found.add(name)
            _walk(value, hidden, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, hidden, found)
    elif isinstance(node, str):
        for name in hidden:
            if name in node.split() or node == name:
                found.add(name)


def _absent(document: dict[str, Any], term: str) -> bool:
    value = document.get(term)
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False
