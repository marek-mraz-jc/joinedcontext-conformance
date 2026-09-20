"""Pure validation functions for AuthZEN access documents and check responses (EP-55, EP-56, EP-59, R51)."""

from __future__ import annotations

import json
from typing import Any, Mapping

CIM_OPERATIONS: frozenset[str] = frozenset({
    "retrieveEntity",
    "queryEntity",
    "createEntity",
    "updateEntity",
    "appendAttrs",
    "deleteEntity",
    "queryTemporal",
    "retrieveTemporal",
    "createSubscription",
    "updateSubscription",
    "deleteSubscription",
    "batchOperation",
})

RESIDUAL_KEYS: frozenset[str] = frozenset({"q", "scopeQ", "geoQ", "temporalQ"})
SUBJECT_KINDS: frozenset[str] = frozenset({"user", "serviceAccount", "role"})


def by_type(doc: Any) -> dict[str, dict[str, Any]]:
    """The permissions of an access document folded per entity type (EP-56).

    The document lists one entry per grant, so two policies reaching one type are two
    entries and the caller's rights on that type are their union. Everything that compares
    the document with another representation compares these folds, never the entry order.
    """
    folded: dict[str, dict[str, Any]] = {}
    if not isinstance(doc, dict):
        return folded
    for entry in doc.get("permissions") or []:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        name = resource.get("type") if isinstance(resource, dict) else None
        if not isinstance(name, str) or not name:
            continue
        seen = folded.setdefault(
            name, {"operations": set(), "attributes": set(), "full_attributes": False, "constraints": {}}
        )
        actions = entry.get("actions")
        if actions == "*":
            seen["operations"].update(CIM_OPERATIONS)
        elif isinstance(actions, list):
            seen["operations"].update(a for a in actions if isinstance(a, str))
        attributes = entry.get("attributes")
        if attributes == "*":
            seen["full_attributes"] = True
        elif isinstance(attributes, list):
            seen["attributes"].update(a for a in attributes if isinstance(a, str))
        constraints = entry.get("constraints")
        if isinstance(constraints, dict):
            for key, value in constraints.items():
                seen["constraints"][key] = value
    return folded


def document_violations(doc: Any) -> list[str]:
    """Validate that doc conforms to the EP-56 / R51 AuthZEN resource-search shape."""
    violations: list[str] = []
    if not isinstance(doc, dict):
        return ["document must be a JSON object"]

    subject = doc.get("subject")
    if not isinstance(subject, dict):
        violations.append("missing or invalid subject object")
    else:
        subject_id = subject.get("id")
        if not isinstance(subject_id, str) or not subject_id.strip():
            violations.append("subject.id must be a non-empty string")
        kind = subject.get("type")
        if kind not in SUBJECT_KINDS:
            violations.append(f"subject.type must be one of {sorted(SUBJECT_KINDS)}, got {kind!r}")

    resource = doc.get("resource")
    if not isinstance(resource, dict):
        violations.append("missing or invalid resource object")
    else:
        if resource.get("type") != "endpoint":
            violations.append(f"resource.type must be 'endpoint', got {resource.get('type')!r}")
        for field in ("id", "space"):
            value = resource.get(field)
            if not isinstance(value, str) or not value.strip():
                violations.append(f"resource.{field} must be a non-empty string")

    for name in ("permissions", "prohibitions"):
        rules = doc.get(name)
        if not isinstance(rules, list):
            violations.append(f"{name} must be a list")
            continue
        for index, entry in enumerate(rules):
            violations.extend(f"{name}[{index}]: {v}" for v in _entry_violations(entry))

    if not doc.get("permissions") and not doc.get("prohibitions"):
        violations.append("a caller with no grant at all should not reach the document")

    limits = doc.get("limits")
    if limits is not None:
        if not isinstance(limits, dict):
            violations.append("limits must be a mapping")
        else:
            rate = limits.get("requestsPerMinute")
            if not isinstance(rate, int) or isinstance(rate, bool) or rate < 1:
                violations.append(f"limits.requestsPerMinute must be a positive integer, got {rate!r}")

    return violations


def _entry_violations(entry: Any) -> list[str]:
    """One permission or prohibition entry (EP-56)."""
    violations: list[str] = []
    if not isinstance(entry, dict):
        return ["entry must be a mapping"]

    resource = entry.get("resource")
    if not isinstance(resource, dict):
        violations.append("resource must be a mapping")
    else:
        entity_type = resource.get("type")
        if not isinstance(entity_type, str) or not entity_type.strip():
            violations.append("resource.type must be a non-empty string")
        patterns = resource.get("idPatterns")
        if patterns is not None and (
            not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns)
        ):
            violations.append("resource.idPatterns must be a list of non-empty strings")

    actions = entry.get("actions")
    if actions == "*":
        pass
    elif not isinstance(actions, list) or not actions:
        violations.append("actions must be a non-empty list, or '*'")
    else:
        unknown = [a for a in actions if a not in CIM_OPERATIONS]
        if unknown:
            violations.append(f"invalid CIM 009 operations: {unknown}")

    attributes = entry.get("attributes")
    if attributes == "*":
        pass
    elif not isinstance(attributes, list) or not all(isinstance(a, str) for a in attributes):
        violations.append("attributes must be a list of strings, or '*'")
    elif len(attributes) != len(set(attributes)):
        violations.append("duplicate attributes")

    constraints = entry.get("constraints")
    if constraints is None:
        return violations
    if not isinstance(constraints, dict):
        violations.append("constraints must be a mapping")
        return violations
    unexpected = set(constraints) - RESIDUAL_KEYS
    if unexpected:
        violations.append(f"unexpected constraint keys: {sorted(unexpected)}")
    for key, value in constraints.items():
        if key in RESIDUAL_KEYS and not isinstance(value, str):
            violations.append(f"constraints[{key!r}] must be a string")

    return violations


def consistency_violations(doc: Any) -> list[str]:
    """What an entry may not say about itself (EP-56).

    An unconstrained grant is `"attributes": "*"` with no constraints; there is no `full`
    flag to disagree with them. What is left to contradict is a grant that grants nothing:
    an empty action list, an empty attribute list, or a residual clause with no filter in it.
    """
    violations: list[str] = []
    if not isinstance(doc, dict):
        return ["document must be a mapping"]

    for name in ("permissions", "prohibitions"):
        for index, entry in enumerate(doc.get(name) or []):
            if not isinstance(entry, dict):
                continue
            where = f"{name}[{index}]"
            if entry.get("actions") is not None and entry.get("actions") != "*" and not entry.get("actions"):
                violations.append(f"{where}: an entry that allows no operation is not a rule")
            attributes = entry.get("attributes")
            if isinstance(attributes, list) and not attributes:
                violations.append(
                    f"{where}: an empty attribute list reads as a grant to nothing; "
                    "a grant over every attribute is '*'"
                )
            constraints = entry.get("constraints")
            if isinstance(constraints, dict):
                for key, value in constraints.items():
                    if isinstance(value, str) and not value.strip():
                        violations.append(f"{where}: constraint {key} is empty")

    return violations


def disclosure_violations(
    doc: Any, forbidden_types: list[str] | set[str], hidden_attrs: list[str] | set[str]
) -> list[str]:
    """EP-59 / R20: forbidden types and hidden attributes must not be disclosed."""
    violations: list[str] = []
    if not isinstance(doc, dict):
        return ["document must be a mapping"]

    folded = by_type(doc)
    for forbidden in forbidden_types:
        if forbidden in folded:
            violations.append(f"forbidden type {forbidden} disclosed in permissions")

    for hidden in hidden_attrs:
        for type_name, grants in folded.items():
            if hidden in grants["attributes"]:
                violations.append(f"hidden attribute {hidden} disclosed in type {type_name}")

    serialized = json.dumps(doc)
    for hidden in hidden_attrs:
        if f'"{hidden}"' in serialized and not any(hidden in v for v in violations):
            violations.append(f"hidden attribute {hidden} disclosed in serialized document")

    return violations


def check_response_violations(request: Mapping[str, Any], response: Any) -> list[str]:
    """Validate AuthZEN evaluation check request and response (R51, R20)."""
    violations: list[str] = []
    if not isinstance(request, (dict, Mapping)):
        return ["request must be a mapping"]
    for req_field in ("subject", "action", "resource"):
        if req_field not in request:
            violations.append(f"request missing required field {req_field}")

    if not isinstance(response, dict):
        return ["response must be a JSON object"]

    decision = response.get("decision")
    if not isinstance(decision, bool):
        violations.append("response decision must be a boolean")

    if decision is False:
        resp_str = json.dumps(response)
        if "urn:ngsi-ld:Policy" in resp_str or "policy" in response or "policyRef" in response:
            violations.append("denial response leaks policy identification (R20)")
        context = response.get("context")
        if isinstance(context, dict):
            for k, v in context.items():
                if isinstance(v, str) and ("Policy:" in v or "urn:ngsi-ld:Policy" in v):
                    violations.append(f"context.{k} leaks policy text: {v}")

    return violations
