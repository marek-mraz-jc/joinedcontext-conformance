"""Pure loader and validator for the prompt injection corpus (T-0080).

Validates vector schema, uniqueness, coverage, and policy constraints without pytest dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import yaml

_MUST_NOT_PATTERN = re.compile(r"^(call|egress|leak|escalate|write|exec):[a-z0-9_.-]+$")
_VALID_EXPECT = {"literal", "refused"}
_VALID_REQUIREMENT = {"AG-20", "AG-21", "TS-25"}

# Every surface the assistant reads, and the one name each is filed under. A placement outside
# this set is a misspelling, and a misspelled surface is a surface nothing covers: the coverage
# tests count vectors by this name (T-1691).
_VALID_PLACEMENT = {
    "attribute-value",
    "commit-message",
    "description",
    "entity-name",
    "file-upload",
    "observation-payload",
    "prior-conversation",
    "schema-annotation",
    "tool-output",
    "tool-parameter",
}


@dataclass(frozen=True)
class Vector:
    id: str
    family: str
    placement: str
    requirement: str
    payload: str
    must_not: tuple[str, ...]
    expect: str
    parameter: str = ""   # tool argument the payload belongs in; empty means q and type


def load(path: str | Path) -> list[Vector]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Corpus file does not exist: {p}")
    content = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(content, dict) or "vectors" not in content:
        raise ValueError(f"Corpus at {p} must be a YAML document with a top-level 'vectors' list")

    raw_vectors = content.get("vectors")
    if not isinstance(raw_vectors, list):
        raise ValueError(f"'vectors' in {p} must be a list, got {type(raw_vectors).__name__}")

    vectors: list[Vector] = []
    required_keys = {"id", "family", "placement", "requirement", "payload", "must_not", "expect"}
    for idx, item in enumerate(raw_vectors):
        if not isinstance(item, dict):
            raise ValueError(f"Vector at index {idx} is not a dictionary: {item!r:.100}")
        vec_id = str(item.get("id") or f"index-{idx}")
        missing = required_keys - set(item.keys())
        if missing:
            raise ValueError(f"Vector '{vec_id}' missing required fields: {sorted(missing)}")

        raw_must_not = item.get("must_not")
        if not isinstance(raw_must_not, list):
            raise ValueError(f"Vector '{vec_id}' field 'must_not' must be a list")

        payload_val = item.get("payload")
        if payload_val is None:
            raise ValueError(f"Vector '{vec_id}' field 'payload' cannot be null")

        # `repeat` keeps an oversized payload readable: the file holds one unit, the loader
        # hands the tests the full length the schema limit is supposed to refuse.
        repeat = item.get("repeat", 1)
        if not isinstance(repeat, int) or isinstance(repeat, bool) or not 1 <= repeat <= 200_000:
            raise ValueError(f"Vector '{vec_id}' field 'repeat' must be an integer between 1 and 200000")

        vectors.append(
            Vector(
                id=str(item["id"]),
                family=str(item["family"]),
                placement=str(item["placement"]),
                requirement=str(item["requirement"]),
                payload=str(payload_val) * repeat,
                must_not=tuple(str(mn) for mn in raw_must_not),
                expect=str(item["expect"]),
                parameter=str(item.get("parameter", "")),
            )
        )
    return vectors


def families(vectors: list[Vector]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in vectors:
        counts[v.family] = counts.get(v.family, 0) + 1
    return counts


def corpus_violations(vectors: list[Vector]) -> list[str]:
    violations: list[str] = []

    if len(vectors) < 50:
        violations.append(f"Corpus contains {len(vectors)} vectors; minimum required is 50")

    seen_ids: set[str] = set()
    for v in vectors:
        if v.id in seen_ids:
            violations.append(f"Duplicate vector id: '{v.id}'")
        seen_ids.add(v.id)

    seen_payloads: dict[str, str] = {}
    for v in vectors:
        if v.payload in seen_payloads:
            violations.append(f"Duplicate payload between '{v.id}' and '{seen_payloads[v.payload]}'")
        else:
            seen_payloads[v.payload] = v.id

        if len(v.payload) < 20:
            violations.append(
                f"Vector '{v.id}' payload is shorter than 20 characters (len={len(v.payload)})"
            )

        if not v.must_not:
            violations.append(f"Vector '{v.id}' has empty 'must_not' list")

        for mn in v.must_not:
            if not _MUST_NOT_PATTERN.match(mn):
                violations.append(
                    f"Vector '{v.id}' must_not entry '{mn}' does not match required pattern"
                )

        if v.expect not in _VALID_EXPECT:
            violations.append(
                f"Vector '{v.id}' expect '{v.expect}' outside allowed {_VALID_EXPECT}"
            )

        if v.requirement not in _VALID_REQUIREMENT:
            violations.append(
                f"Vector '{v.id}' requirement '{v.requirement}' outside allowed {_VALID_REQUIREMENT}"
            )

        if v.placement not in _VALID_PLACEMENT:
            violations.append(
                f"Vector '{v.id}' placement '{v.placement}' outside allowed {sorted(_VALID_PLACEMENT)}"
            )

    fam_counts = families(vectors)
    for fam, count in fam_counts.items():
        if count < 2:
            violations.append(f"Family '{fam}' has fewer than 2 vectors (found {count})")

    return violations
