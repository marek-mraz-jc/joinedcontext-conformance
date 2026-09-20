"""Pure functions for discovering and validating LinkML DataModel artifacts (T-0078).

Verifies DM-01, DM-02, DM-03, DM-22, DM-26, DM-43, DM-46, and TS-18.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import pyshacl
import rdflib
import yaml

FORBIDDEN_KEYWORDS_POST_DRAFT7 = frozenset(
    {
        "$defs",
        "unevaluatedProperties",
        "unevaluatedItems",
        "dependentRequired",
        "dependentSchemas",
        "prefixItems",
        "minContains",
        "maxContains",
        "$recursiveRef",
        "$dynamicRef",
    }
)

VALID_LIFECYCLES = frozenset({"draft", "published", "deprecated", "retired"})
SEMVER_REGEX = re.compile(r"^(\d+)\.\d+\.\d+(?:-[\w.-]+)?(?:\+[\w.-]+)?$")
MAJOR_IN_PATH_REGEX = re.compile(r"v(\d+)")


@dataclass(frozen=True)
class DataModel:
    manifest: Path
    name: str
    version: str
    major: str
    lifecycle: str
    linkml: Path
    artifacts: dict[str, Path]
    missing: tuple[str, ...] = field(default_factory=tuple)


def discover(root: Path) -> list[DataModel]:
    """Discover all kind: DataModel manifests under root."""
    if not root.is_dir():
        return []

    models: list[DataModel] = []
    candidates = sorted(list(root.rglob("*.yaml")) + list(root.rglob("*.yml")))

    for path in candidates:
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(content, dict) or content.get("kind") != "DataModel":
            continue

        metadata = content.get("metadata") or {}
        spec = content.get("spec") or {}

        name = str(metadata.get("name") or path.stem)
        version = str(spec.get("version") or "")
        lifecycle = str(spec.get("lifecycle") or "draft")

        major_match = SEMVER_REGEX.match(version)
        major = major_match.group(1) if major_match else version.split(".")[0]

        manifest_dir = path.parent
        linkml_raw = spec.get("linkml")
        linkml_path = (manifest_dir / linkml_raw).resolve() if linkml_raw else (manifest_dir / f"{name}.linkml.yaml")

        artifacts_raw = spec.get("artifacts") or {}
        artifacts: dict[str, Path] = {}
        missing: list[str] = []

        if not linkml_path.exists():
            missing.append(f"linkml source not found: {linkml_path}")

        if not artifacts_raw:
            missing.append("spec.artifacts is missing or empty")

        for key, rel_path in artifacts_raw.items():
            resolved = (manifest_dir / rel_path).resolve()
            artifacts[key] = resolved
            if not resolved.exists():
                missing.append(f"artifact '{key}' not found: {resolved}")

        models.append(
            DataModel(
                manifest=path.resolve(),
                name=name,
                version=version,
                major=major,
                lifecycle=lifecycle,
                linkml=linkml_path,
                artifacts=artifacts,
                missing=tuple(missing),
            )
        )

    return models


def manifest_violations(model: DataModel) -> list[str]:
    """Check DM-22 (major version alignment in paths), DM-26 (lifecycle), and missing files."""
    violations: list[str] = []

    if not SEMVER_REGEX.match(model.version):
        violations.append(
            f"spec.version '{model.version}' is not a valid semantic version (MAJOR.MINOR.PATCH)"
        )

    if model.lifecycle not in VALID_LIFECYCLES:
        violations.append(
            f"spec.lifecycle '{model.lifecycle}' is invalid; must be one of {sorted(VALID_LIFECYCLES)}"
        )

    for missing_item in model.missing:
        violations.append(f"missing referenced file: {missing_item}")

    for key, path in model.artifacts.items():
        rel_str = str(path.relative_to(model.manifest.parent) if path.is_relative_to(model.manifest.parent) else path)
        found_versions = MAJOR_IN_PATH_REGEX.findall(rel_str)
        for ver in found_versions:
            if ver != model.major:
                violations.append(
                    f"artifact '{key}' path '{rel_str}' has version v{ver} which does not match model major v{model.major}"
                )

    return violations


def draft07_violations(schema: dict[str, Any]) -> list[str]:
    """Validate that schema is draft-07 and contains no 2019-09 or 2020-12 keywords (DM-03)."""
    violations: list[str] = []
    schema_uri = schema.get("$schema", "")
    if "draft-07" not in schema_uri and "draft/07" not in schema_uri:
        violations.append(
            f"$schema declaration must reference draft-07, found: '{schema_uri}'"
        )

    def _walk(node: Any, current_path: str, in_properties: bool) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                new_path = f"{current_path}.{key}" if current_path else key
                if not in_properties and key in FORBIDDEN_KEYWORDS_POST_DRAFT7:
                    violations.append(
                        f"forbidden post-draft-07 keyword '{key}' found at {current_path or '<root>'}"
                    )
                if key in ("properties", "patternProperties") and isinstance(val, dict):
                    for prop_name, prop_schema in val.items():
                        _walk(prop_schema, f"{new_path}.{prop_name}", False)
                else:
                    _walk(val, new_path, False)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, f"{current_path}[{idx}]", False)

    _walk(schema, "", False)
    return sorted(violations)


def example_violations(example: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate example against JSON Schema draft-07 (TS-18)."""
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
    violations: list[str] = []
    for err in errors[:20]:
        path_str = ".".join(str(p) for p in err.path) if err.path else "<root>"
        violations.append(f"{path_str}: {err.message}")
    return violations


def shacl_violations(
    example_path: Path | str,
    context_path: Path | str,
    shapes_path: Path | str,
    example_data: dict[str, Any] | None = None,
) -> list[str]:
    """Expand example with served @context and validate against SHACL shapes (DM-43, DM-46)."""
    ex_path = Path(example_path)
    ctx_path = Path(context_path)
    sh_path = Path(shapes_path)

    if not ex_path.exists() and example_data is None:
        return [f"example file not found: {ex_path}"]
    if not ctx_path.exists():
        return [f"context file not found: {ctx_path}"]
    if not sh_path.exists():
        return [f"shapes file not found: {sh_path}"]

    if example_data is None:
        try:
            example_data = json.loads(ex_path.read_text(encoding="utf-8"))
        except Exception as e:
            return [f"failed to parse example JSON: {e}"]

    try:
        context_json = json.loads(ctx_path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"failed to parse context JSON: {e}"]

    try:
        shapes_ttl = sh_path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"failed to read SHACL shapes ({sh_path}): {e}"]

    return shacl_violations_of(example_data, context_json, shapes_ttl)


def shacl_violations_of(
    entity: dict[str, Any],
    context_json: dict[str, Any],
    shapes_ttl: str,
) -> list[str]:
    """The same check on documents already in memory, for what an endpoint serves (T-0337).

    A live endpoint hands back the entity, the `@context` and the shapes over HTTP, and
    those three are what DM-46 is about; reading them from disk first would only prove that
    a temporary file round-trips.
    """
    context_val = context_json.get("@context", context_json)
    payload = {"@context": context_val, **entity}

    data_graph = rdflib.Graph()
    try:
        data_graph.parse(data=json.dumps(payload), format="json-ld")
    except Exception as e:
        return [f"failed to parse expanded JSON-LD data graph: {e}"]

    shapes_graph = rdflib.Graph()
    try:
        shapes_graph.parse(data=shapes_ttl, format="turtle")
    except Exception as e:
        return [f"failed to parse SHACL shapes graph: {e}"]

    # An entity whose @context does not alias `type` to `@type` expands to a node with no rdf:type.
    # sh:targetClass then selects nothing, pySHACL reports conformance, and the suite would pass
    # while validating an empty graph — the one way this check can lie.
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    target_classes = set(shapes_graph.objects(None, sh.targetClass))
    if target_classes:
        typed = set(data_graph.objects(None, rdflib.RDF.type))
        if not (target_classes & typed):
            return [
                "the expanded example has no node of any sh:targetClass, so the shapes validated "
                f"nothing: shapes target {sorted(str(c) for c in target_classes)}, the example "
                f"expanded to types {sorted(str(t) for t in typed) or '[]'} — check that the served "
                "@context aliases `type` to `@type` and that the class IRIs match"
            ]

    try:
        conforms, results_graph, results_text = pyshacl.validate(
            data_graph, shacl_graph=shapes_graph, advanced=True, inference="none"
        )
    except Exception as e:
        return [f"pySHACL validation failed with error: {e}"]

    if conforms:
        return []

    violations: list[str] = []
    sh_ns = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    for result in results_graph.subjects(rdflib.RDF.type, sh_ns.ValidationResult):
        focus = results_graph.value(result, sh_ns.focusNode)
        path = results_graph.value(result, sh_ns.resultPath)
        msg = results_graph.value(result, sh_ns.resultMessage)
        violations.append(f"Focus node <{focus}> path <{path}>: {msg}")

    if not violations and results_text:
        violations.append(results_text.strip())

    return sorted(violations)


def regeneration_diff(
    model: DataModel, command: list[str], workdir: Path
) -> list[str]:
    """Run Model Tools command and assert generated files match committed files byte-for-byte (DM-02)."""
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = command + [str(model.linkml), "--out", str(workdir)]

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        last_stderr = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "no stderr"
        return [f"generator failed with exit code {res.returncode}: {last_stderr}"]

    violations: list[str] = []
    generated_files = {p.name: p for p in workdir.rglob("*") if p.is_file()}
    committed_files = {p.name: p for p in model.artifacts.values() if p.is_file()}

    for name, p_comm in committed_files.items():
        if name not in generated_files:
            violations.append(f"generator did not produce expected artifact: {name}")
        else:
            p_gen = generated_files[name]
            if p_comm.read_bytes() != p_gen.read_bytes():
                violations.append(f"regenerated artifact differs from committed: {name}")

    for name in generated_files:
        if name not in committed_files:
            violations.append(f"generator produced uncommitted artifact: {name}")

    return sorted(violations)
