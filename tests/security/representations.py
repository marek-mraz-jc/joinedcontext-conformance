from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import io
import json
import math
from typing import Any

PARTIAL = {"sta"}


@dataclass(frozen=True)
class Record:
    """One entity as a representation sees it, attribute names flattened."""

    id: str
    attrs: dict[str, object]


def _flatten_dict(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dict(v, key))
        else:
            out[key] = v
    return out


def from_ngsild(payload: Any) -> list[Record]:
    """Parse NGSI-LD entity array into Records unwrapping Property/GeoProperty/Relationship."""
    entities = payload if isinstance(payload, list) else []
    records: list[Record] = []
    for entity in entities:
        if not isinstance(entity, dict) or "id" not in entity:
            continue
        entity_id = str(entity["id"])
        attrs: dict[str, object] = {}
        for k, v in entity.items():
            if k in ("id", "type", "@context"):
                continue
            if isinstance(v, dict):
                v_type = v.get("type")
                if v_type == "Property":
                    val = v.get("value")
                elif v_type == "GeoProperty":
                    val = v.get("value")
                elif v_type == "Relationship":
                    val = v.get("object")
                else:
                    val = v
                if isinstance(val, dict):
                    for sub_k, sub_v in _flatten_dict(val, k).items():
                        attrs[sub_k] = sub_v
                else:
                    attrs[k] = val
            else:
                attrs[k] = v
        records.append(Record(id=entity_id, attrs=attrs))
    return records


def from_csv(text: str) -> list[Record]:
    """Parse CSV into Records; empty strings are absent attributes, not empty values (EP-08, TS-03)."""
    records: list[Record] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        rec_id = row.get("id")
        if not rec_id:
            continue
        attrs: dict[str, object] = {}
        for k, v in row.items():
            if k == "id" or v is None or v == "":
                continue
            attrs[k] = v
        records.append(Record(id=rec_id, attrs=attrs))
    return records


def from_geojson(payload: Any, geo_attr: str = "location") -> list[Record]:
    """Parse GeoJSON FeatureCollection into Records."""
    features = payload.get("features", []) if isinstance(payload, dict) else []
    records: list[Record] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        rec_id = feature.get("id") or props.get("id")
        if not rec_id:
            continue
        attrs: dict[str, object] = {}
        for k, v in props.items():
            if k == "id":
                continue
            if isinstance(v, dict):
                for sub_k, sub_v in _flatten_dict(v, k).items():
                    attrs[sub_k] = sub_v
            else:
                attrs[k] = v
        if "geometry" in feature and feature["geometry"] is not None:
            attrs[geo_attr] = feature["geometry"]
        records.append(Record(id=str(rec_id), attrs=attrs))
    return records


def from_ogc_features(payload: Any, geo_attr: str = "location") -> list[Record]:
    # OGC API Features serves GeoJSON FeatureCollections, so reuse from_geojson directly
    return from_geojson(payload, geo_attr=geo_attr)


def from_sta(payload: Any, id_field: str = "@iot.id") -> list[Record]:
    """SensorThings answers {"value": [...]}.

    STA is not entity-shaped (records represent observations rather than whole entities),
    so the parity it can carry is the set of ids it exposes and the attribute names it mentions.
    We return one Record per observation with id from id_field and flattened attrs minus navigation links.
    """
    items = payload.get("value", []) if isinstance(payload, dict) else []
    records: list[Record] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rec_id = item.get(id_field)
        if rec_id is None:
            continue
        attrs: dict[str, object] = {}
        flat = _flatten_dict(item)
        for k, v in flat.items():
            if "navigationLink" in k or k.endswith("@iot.selfLink") or k == id_field:
                continue
            attrs[k] = v
        records.append(Record(id=str(rec_id), attrs=attrs))
    return records


def normalise(value: Any) -> Any:
    """Normalize a value for representation parity comparisons."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 8)
    if isinstance(value, str):
        v = value.strip()
        if (v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]")):
            try:
                parsed = json.loads(v)
                return normalise(parsed)
            except Exception:
                pass
        try:
            num = float(v)
            if not math.isnan(num) and not math.isinf(num):
                return round(num, 8)
        except ValueError:
            pass
        iso_cand = v[:-1] if v.endswith("Z") else v
        try:
            dt = datetime.fromisoformat(iso_cand)
            return dt.isoformat()
        except ValueError:
            pass
        return v
    if isinstance(value, dict):
        return {k: normalise(val) for k, val in sorted(value.items())}
    if isinstance(value, list):
        return [normalise(val) for val in value]
    return json.dumps(value, sort_keys=True)


def parity_violations(
    by_representation: dict[str, list[Record]],
    *,
    hidden_attrs: set[str],
    id_scope: set[str] | None = None,
) -> list[str]:
    violations: list[str] = []

    non_empty_reps = {name: recs for name, recs in by_representation.items() if len(recs) > 0}
    if len(non_empty_reps) < 2:
        return [f"fewer than two representations with data: found {list(non_empty_reps.keys())}"]

    rep_names = sorted(by_representation.keys())
    for r1 in rep_names:
        for r2 in rep_names:
            if r1 >= r2:
                continue
            c1 = len(by_representation[r1])
            c2 = len(by_representation[r2])
            if (c1 == 0 and c2 > 0) or (c2 == 0 and c1 > 0):
                violations.append(
                    f"representation '{r1}' returned {c1} records while '{r2}' returned {c2} records"
                )

    ids_by_rep: dict[str, set[str]] = {}
    for name, recs in by_representation.items():
        s = {r.id for r in recs}
        if id_scope is not None:
            s = s & id_scope
        ids_by_rep[name] = s

    # Compare ID sets across entity representations (excluding PARTIAL like sta)
    entity_reps = [r for r in rep_names if r not in PARTIAL and r in non_empty_reps]
    for i, r1 in enumerate(entity_reps):
        for r2 in entity_reps[i + 1 :]:
            diff1 = sorted(ids_by_rep[r1] - ids_by_rep[r2])
            diff2 = sorted(ids_by_rep[r2] - ids_by_rep[r1])
            if diff1:
                violations.append(
                    f"ids present in '{r1}' but missing in '{r2}': {diff1[:5]}"
                )
            if diff2:
                violations.append(
                    f"ids present in '{r2}' but missing in '{r1}': {diff2[:5]}"
                )

    # Compare attribute values across entity representations for shared IDs
    recs_by_rep_id: dict[str, dict[str, Record]] = {
        name: {r.id: r for r in recs} for name, recs in by_representation.items()
    }
    for i, r1 in enumerate(entity_reps):
        for r2 in entity_reps[i + 1 :]:
            shared_ids = sorted(ids_by_rep[r1] & ids_by_rep[r2])
            for sid in shared_ids:
                a1 = recs_by_rep_id[r1][sid].attrs
                a2 = recs_by_rep_id[r2][sid].attrs
                shared_attrs = sorted(set(a1.keys()) & set(a2.keys()))
                for attr in shared_attrs:
                    v1 = normalise(a1[attr])
                    v2 = normalise(a2[attr])
                    if v1 != v2:
                        violations.append(
                            f"attribute value mismatch for id '{sid}' attr '{attr}': "
                            f"'{r1}' had {v1!r} vs '{r2}' had {v2!r}"
                        )

    # Hidden attributes leak check
    for name, recs in by_representation.items():
        for rec in recs:
            for attr in rec.attrs:
                for h in hidden_attrs:
                    if attr == h or attr.startswith(f"{h}.") or attr.endswith(f".{h}"):
                        violations.append(
                            f"hidden attribute '{h}' leaked in representation '{name}' for id '{rec.id}'"
                        )

    return violations
