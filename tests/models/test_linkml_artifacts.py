"""Pytest suite for LinkML DataModel artifacts conformance (T-0078).

Verifies TS-18, DM-02, DM-03, DM-22, DM-26, DM-43, DM-46.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from artifacts import (
    DataModel,
    discover,
    draft07_violations,
    example_violations,
    manifest_violations,
    regeneration_diff,
    shacl_violations,
)


def _models_root() -> Path:
    env_dir = os.environ.get("PROJECTS_DIR")
    if not env_dir:
        pytest.skip("PROJECTS_DIR environment variable is not set")
    p = Path(env_dir).resolve()
    if not p.is_dir():
        pytest.skip(f"PROJECTS_DIR does not exist or is not a directory: {p}")
    return p


def _all_models() -> list[DataModel]:
    env_dir = os.environ.get("PROJECTS_DIR")
    if not env_dir:
        return []
    p = Path(env_dir).resolve()
    return discover(p) if p.is_dir() else []


MODELS = _all_models()


def test_the_projects_tree_holds_at_least_one_data_model() -> None:
    root = _models_root()
    models = discover(root)
    assert models, f"no kind: DataModel manifests found under {root}"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_dm02_dm22_the_manifest_and_its_artifacts_agree(model: DataModel) -> None:
    violations = manifest_violations(model)
    assert not violations, f"Manifest violations for {model.name}:\n" + "\n".join(violations)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_dm03_the_committed_json_schema_is_draft_07(model: DataModel) -> None:
    schema_path = model.artifacts.get("jsonSchema")
    assert schema_path and schema_path.is_file(), f"Model {model.name} is missing jsonSchema artifact"
    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    violations = draft07_violations(schema_data)
    assert not violations, (
        f"JSON Schema for {model.name} is not valid draft-07 (DM-03):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_ts18_the_committed_example_validates_against_the_committed_json_schema(model: DataModel) -> None:
    schema_path = model.artifacts.get("jsonSchema")
    example_path = model.artifacts.get("example")
    assert schema_path and schema_path.is_file(), f"Model {model.name} is missing jsonSchema artifact"
    assert example_path and example_path.is_file(), f"Model {model.name} is missing example artifact"

    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    example_data = json.loads(example_path.read_text(encoding="utf-8"))
    violations = example_violations(example_data, schema_data)
    assert not violations, (
        f"Committed example does not validate against committed JSON Schema (TS-18):\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_dm46_pyshacl_accepts_the_example_expanded_with_the_served_context(model: DataModel) -> None:
    if model.lifecycle != "published":
        pytest.skip(f"Model {model.name} is '{model.lifecycle}' (only 'published' models require DM-46)")

    example_path = model.artifacts.get("example")
    context_path = model.artifacts.get("context")
    shapes_path = model.artifacts.get("shacl")

    if not shapes_path or not shapes_path.is_file():
        pytest.skip(
            f"Model {model.name} has no spec.artifacts.shacl artifact; generate with MODEL_TOOLS_CMD"
        )
    assert example_path and example_path.is_file(), f"Missing example artifact for {model.name}"
    assert context_path and context_path.is_file(), f"Missing context artifact for {model.name}"

    violations = shacl_violations(example_path, context_path, shapes_path)
    assert not violations, (
        f"pySHACL rejected the expanded example entity (DM-46):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_dm46_the_shapes_reject_an_entity_that_breaks_the_model(model: DataModel) -> None:
    if model.lifecycle != "published":
        pytest.skip(f"Model {model.name} is '{model.lifecycle}'")

    example_path = model.artifacts.get("example")
    context_path = model.artifacts.get("context")
    shapes_path = model.artifacts.get("shacl")
    schema_path = model.artifacts.get("jsonSchema")

    if not shapes_path or not shapes_path.is_file():
        pytest.skip(
            f"Model {model.name} has no spec.artifacts.shacl artifact; generate with MODEL_TOOLS_CMD"
        )
    assert example_path and example_path.is_file(), f"Missing example artifact for {model.name}"
    assert context_path and context_path.is_file(), f"Missing context artifact for {model.name}"
    assert schema_path and schema_path.is_file(), f"Missing jsonSchema artifact for {model.name}"

    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    example_data = json.loads(example_path.read_text(encoding="utf-8"))

    required_slots = [
        slot for slot in schema_data.get("required", []) if slot not in ("id", "type")
    ]
    if not required_slots:
        pytest.skip(f"Model {model.name} declares no required slots beyond id/type to break")

    broken_entity: dict[str, Any] = json.loads(json.dumps(example_data))

    slot_to_remove = required_slots[0]
    broken_entity.pop(slot_to_remove, None)

    for prop_name, prop_def in schema_data.get("properties", {}).items():
        if prop_name in ("id", "type", slot_to_remove):
            continue
        if isinstance(prop_def, dict) and prop_name in broken_entity:
            val_schema = prop_def.get("properties", {}).get("value", {})
            if val_schema.get("type") in ("number", "integer"):
                broken_entity[prop_name] = {"type": "Property", "value": "not-a-number"}
                break

    violations = shacl_violations(example_path, context_path, shapes_path, example_data=broken_entity)
    assert violations, (
        f"pySHACL accepted broken entity for {model.name} (shapes failed to reject broken data)"
    )


@pytest.mark.parametrize("model", MODELS, ids=lambda m: f"{m.name}-v{m.major}")
def test_dm02_regenerating_the_artifacts_reproduces_the_committed_files(
    model: DataModel, tmp_path: Path
) -> None:
    cmd_raw = os.environ.get("MODEL_TOOLS_CMD")
    if not cmd_raw:
        pytest.skip("MODEL_TOOLS_CMD environment variable is not set")

    violations = regeneration_diff(model, cmd_raw.split(), tmp_path)
    assert not violations, (
        f"Regenerated artifacts differ from committed ones (DM-02):\n" + "\n".join(violations)
    )
