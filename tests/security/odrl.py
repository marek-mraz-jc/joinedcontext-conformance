"""ODRL 2.2 mapping and round-trip verification in the ngsi-ld: profile (R26, R52, EP-57)."""

from __future__ import annotations

import json
from typing import Any, Mapping

from access_doc import by_type

# The profile the gateway serves and API/02 documents: the JSON-LD context in `@context`,
# and the namespace its terms resolve in (T-0783, EP-57, R52).
PROFILE = "https://joinedcontext.com/odrl/ngsi-ld/v1/context.jsonld"
PROFILE_NAMESPACE = "https://joinedcontext.com/odrl/ngsi-ld/v1#"
ODRL_CONTEXT = "http://www.w3.org/ns/odrl.jsonld"

# Exactly the operands `Architecture/04` defines for this profile, and nothing else (T-0943).
# `entities` is not among them: the type a permission reaches is its `target`, not a
# refinement of it. `dateTime` is ODRL 2.2's own core operand and is allowed unprefixed.
_BASE_LEFT_OPERANDS = {
    "attrs",
    "id",
    "idPattern",
    "q",
    "scopeQ",
    "geoQ",
    "temporalQ",
}

LEFT_OPERANDS: frozenset[str] = frozenset(
    _BASE_LEFT_OPERANDS | {f"ngsi-ld:{op}" for op in _BASE_LEFT_OPERANDS} | {"dateTime"}
)

VALID_POLICY_TYPES: frozenset[str] = frozenset({
    "Policy",
    "Set",
    "Offer",
    "Agreement",
    "odrl:Policy",
    "odrl:Set",
    "odrl:Offer",
    "odrl:Agreement",
})

READ_OPERATIONS = frozenset({
    "retrieveEntity",
    "queryEntity",
    "queryTemporal",
    "retrieveTemporal",
})
WRITE_OPERATIONS = frozenset({
    "createEntity",
    "updateEntity",
    "appendAttrs",
    "deleteEntity",
    "batchOperation",
})


def odrl_violations(policy: Any) -> list[str]:
    """Validate that policy conforms to ODRL 2.2 in the ngsi-ld: profile."""
    violations: list[str] = []
    if not isinstance(policy, dict):
        return ["policy must be a JSON object"]

    ctx = policy.get("@context")
    if not ctx:
        violations.append("missing @context")
    else:
        ctx_str = json.dumps(ctx)
        if PROFILE not in ctx_str:
            violations.append(f"@context must include profile {PROFILE}")
        if "odrl" not in ctx_str.lower():
            violations.append("missing odrl context in @context")

    ptype = policy.get("@type")
    if ptype not in VALID_POLICY_TYPES:
        violations.append(f"invalid @type: {ptype!r}, expected one of {sorted(VALID_POLICY_TYPES)}")

    uid = policy.get("uid")
    if not isinstance(uid, str) or not uid.strip():
        violations.append("missing or empty uid")

    permissions = policy.get("permission")
    if permissions is None or not isinstance(permissions, list):
        violations.append("permission must be a list")
    else:
        for idx, perm in enumerate(permissions):
            if not isinstance(perm, dict):
                violations.append(f"permission[{idx}] must be an object")
                continue
            if "action" not in perm:
                violations.append(f"permission[{idx}] missing action")
            if "target" not in perm:
                violations.append(f"permission[{idx}] missing target")
            # ODRL 2.2 allows a party on the rule or on the policy. One caller reads this
            # document and one organization authored the space's policies, so both sit on
            # the policy; a rule carries its own `assigner` only when the rules disagree
            # (EP-57). Demanding them per rule is how the same grant read twice looks
            # different in RDF for no reason.
            for party in ("assigner", "assignee"):
                if party not in perm and party not in policy:
                    violations.append(
                        f"permission[{idx}]: no {party}, and the policy names none either"
                    )

            constraints = perm.get("constraint", [])
            if isinstance(constraints, list):
                for c_idx, c in enumerate(constraints):
                    if isinstance(c, dict):
                        left_op = c.get("leftOperand")
                        if left_op not in LEFT_OPERANDS:
                            violations.append(
                                f"permission[{idx}].constraint[{c_idx}] invalid leftOperand: {left_op!r}"
                            )
                        if "operator" not in c:
                            violations.append(f"permission[{idx}].constraint[{c_idx}] missing operator")
                        if "rightOperand" not in c:
                            violations.append(f"permission[{idx}].constraint[{c_idx}] missing rightOperand")

            target = perm.get("target")
            if isinstance(target, dict):
                refinements = target.get("refinement", [])
                if isinstance(refinements, list):
                    for r_idx, r in enumerate(refinements):
                        if isinstance(r, dict):
                            left_op = r.get("leftOperand")
                            if left_op not in LEFT_OPERANDS:
                                violations.append(
                                    f"permission[{idx}].target.refinement[{r_idx}] invalid leftOperand: {left_op!r}"
                                )

    return violations


def _clean_term(term: str) -> str:
    if ":" in term:
        return term.rsplit(":", 1)[-1]
    return term


def to_grants(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Fold an ODRL 2.2 policy in the ngsi-ld: profile back into the access-document shape."""
    types_map: dict[str, dict[str, Any]] = {}

    for perm in policy.get("permission", []):
        if not isinstance(perm, dict):
            continue

        target = perm.get("target")
        target_name = ""
        refinements = []
        if isinstance(target, dict):
            # The documented shape is `{"@type": "ngsi-ld:EntityType", "uid": "<Type>"}`
            # (API/02, EP-57); the rest are what other ODRL producers write.
            target_name = (
                target.get("uid")
                or target.get("id")
                or target.get("@id")
                or target.get("name")
                or target.get("type", "")
            )
            refinements = target.get("refinement", [])
        elif isinstance(target, str):
            target_name = target

        target_name = _clean_term(target_name)
        if not target_name:
            continue

        if target_name not in types_map:
            types_map[target_name] = {
                "operations": set(),
                "attrs": {"read": set(), "write": set()},
                "residual": {},
                "full": False,
            }

        entry = types_map[target_name]

        perm_action = perm.get("action")
        actions: list[str] = []
        if isinstance(perm_action, list):
            actions = perm_action
        elif isinstance(perm_action, dict):
            act_id = perm_action.get("@id") or perm_action.get("id") or perm_action.get("name", "")
            actions = [act_id]
        elif isinstance(perm_action, str):
            actions = [perm_action]

        clean_actions = [_clean_term(a) for a in actions if a]
        entry["operations"].update(clean_actions)

        for ref in refinements:
            if not isinstance(ref, dict):
                continue
            left_op = _clean_term(ref.get("leftOperand", ""))
            right_op = ref.get("rightOperand")
            attrs_list: list[str] = right_op if isinstance(right_op, list) else ([str(right_op)] if right_op else [])
            if left_op in {"propertyNames", "relationshipNames", "attrs"}:
                if any(a in READ_OPERATIONS for a in clean_actions) or not any(a in WRITE_OPERATIONS for a in clean_actions):
                    entry["attrs"]["read"].update(attrs_list)
                if any(a in WRITE_OPERATIONS for a in clean_actions):
                    entry["attrs"]["write"].update(attrs_list)

        constraints = perm.get("constraint", [])
        if isinstance(constraints, list):
            for c in constraints:
                if not isinstance(c, dict):
                    continue
                left_op = _clean_term(c.get("leftOperand", ""))
                right_op = c.get("rightOperand")
                if left_op in {"q", "scopeQ", "geoQ", "temporalQ"} and right_op is not None:
                    entry["residual"][left_op] = str(right_op)

    result_types: dict[str, dict[str, Any]] = {}
    for t_name, t_data in types_map.items():
        res = t_data["residual"]
        read_a = sorted(t_data["attrs"]["read"])
        write_a = sorted(t_data["attrs"]["write"])
        is_full = len(res) == 0 and len(read_a) == 0 and len(write_a) == 0
        result_types[t_name] = {
            "operations": sorted(t_data["operations"]),
            "attrs": {"read": read_a, "write": write_a},
            "residual": res,
            "full": is_full,
        }

    return {"types": result_types}


def round_trip_violations(access_doc: Mapping[str, Any], odrl_policy: Mapping[str, Any]) -> list[str]:
    """The two representations carry the same grants (R26, R52, EP-57).

    Both are folded per entity type first — the permissions document lists one entry per
    grant, the ODRL policy one rule per grant — so what is compared is what the caller may
    do, not how either document lays it out. Attributes are compared as one set: the JSON
    document names the slots the grant reaches, and ODRL splits them across read and write
    rules, which is the same projection said twice.
    """
    violations: list[str] = []
    folded = to_grants(odrl_policy).get("types", {})
    documented = by_type(access_doc)

    for name in sorted(set(documented) | set(folded)):
        if name not in folded:
            violations.append(f"type {name}: missing in the ODRL policy")
            continue
        if name not in documented:
            violations.append(f"type {name}: in the ODRL policy and not in the document")
            continue

        doc_grant = documented[name]
        odrl_grant = folded[name]

        doc_ops = set(doc_grant["operations"])
        odrl_ops = set(odrl_grant.get("operations", []))
        if doc_ops - odrl_ops:
            violations.append(f"type {name}: operations lost in ODRL: {sorted(doc_ops - odrl_ops)}")
        if odrl_ops - doc_ops:
            violations.append(f"type {name}: operations only in ODRL: {sorted(odrl_ops - doc_ops)}")

        if not doc_grant["full_attributes"]:
            doc_attrs = set(doc_grant["attributes"])
            odrl_attrs = set(odrl_grant.get("attrs", {}).get("read", [])) | set(
                odrl_grant.get("attrs", {}).get("write", [])
            )
            if doc_attrs - odrl_attrs:
                violations.append(f"type {name}: attributes lost in ODRL: {sorted(doc_attrs - odrl_attrs)}")
            if odrl_attrs - doc_attrs:
                violations.append(f"type {name}: attributes only in ODRL: {sorted(odrl_attrs - doc_attrs)}")

        doc_residual = doc_grant["constraints"]
        odrl_residual = odrl_grant.get("residual", {})
        for key in sorted(set(doc_residual) | set(odrl_residual)):
            if key not in odrl_residual:
                violations.append(f"type {name}: residual {key} lost in ODRL")
            elif key not in doc_residual:
                violations.append(f"type {name}: residual {key} only in ODRL")
            elif " ".join(str(doc_residual[key]).split()) != " ".join(str(odrl_residual[key]).split()):
                violations.append(
                    f"type {name}: residual {key} differs: "
                    f"{doc_residual[key]!r} != {odrl_residual[key]!r}"
                )

    return violations


