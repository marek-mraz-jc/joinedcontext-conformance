"""The LinkML surface of a view, judged by the reference LinkML tooling (Antares T-3512).

Rows of `surfaces-conformance.md` section 5: the served schema (L2) loads and lints clean, the
reference generators accept it, the view's own Entities validate against the served JSON Schema
(L3) and SHACL shapes (L5) read through the served `@context` (L4), and a narrowed view's schema
names nothing the view hides (`effective = view ∧ engine`, ADR-0023 rule 5).
"""

from __future__ import annotations

import json

import jsonschema
import pyshacl
import rdflib
from linkml_runtime.utils.schemaview import SchemaView

from conftest import HIDDEN, tool

# NGSI-LD core terms of an Entity in keyValues (core @context): its id and its type
CORE = {"id": "@id", "type": "@type"}
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")


def test_served_schema_loads(served):
    view = SchemaView(str(served.path("model.linkml.yaml")))
    assert sorted(view.all_classes()) == sorted(served.types), "the catalogue names the classes"
    assert served.types, "the view shows a class: an empty schema would pass everything below"
    lint = tool("linkml-lint", "--ignore-warnings", "--format", "json", str(served.path("model.linkml.yaml")))
    errors = [p for p in json.loads(lint.stdout or "[]") if p["level"] == "error"]
    assert lint.returncode == 0 and not errors, lint.stdout + lint.stderr


def test_reference_generators_accept_the_schema(served):
    yaml = str(served.path("model.linkml.yaml"))
    generated = tool("gen-json-schema", yaml)
    assert generated.returncode == 0, generated.stderr
    defs = json.loads(generated.stdout)["$defs"]
    assert set(served.types) <= set(defs), sorted(defs)
    generated = tool("gen-shacl", yaml)
    assert generated.returncode == 0, generated.stderr
    shapes = rdflib.Graph().parse(data=generated.stdout, format="turtle")
    assert len(set(shapes.objects(None, SH.targetClass))) == len(served.types), generated.stdout


def test_served_json_schema_validates_the_view(view_all, http):
    schema = json.loads(view_all.path("model.schema.json").read_text())
    entities = view_all.entities(http)
    assert {e["type"] for e in entities} == set(view_all.types), "every class has an Entity to judge"
    for entity in entities:
        one = {**schema["definitions"][entity["type"]], "definitions": schema["definitions"]}
        validator = jsonschema.Draft7Validator(one, format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER)
        problems = [f"{'/'.join(map(str, e.path))}: {e.message}" for e in validator.iter_errors(entity)]
        assert not problems, f"{entity['id']}: {problems}"


def test_served_shacl_validates_the_view(view_all, http):
    # view `all` holds every Entity a Relationship of the seed names, so sh:class can hold
    context = json.loads(view_all.path("context.jsonld").read_text())["@context"]
    entities = view_all.entities(http)
    data = rdflib.Graph().parse(
        data=json.dumps({"@context": [CORE, context], "@graph": entities}), format="json-ld"
    )
    shapes = rdflib.Graph().parse(view_all.path("model.shacl.ttl"))
    for target in shapes.objects(None, SH.targetClass):
        assert any(data.subjects(rdflib.RDF.type, target)), f"no Entity is a {target}: nothing judged"
    conforms, _, report = pyshacl.validate(data, shacl_graph=shapes, meta_shacl=True)
    assert conforms, report


def test_hidden_attributes_are_absent_from_the_schema(view_all, view_restricted):
    shown = SchemaView(str(view_all.path("model.linkml.yaml")))
    assert any(HIDDEN in shown.class_slots(c) for c in shown.all_classes()), f"view all shows {HIDDEN}"
    narrowed = SchemaView(str(view_restricted.path("model.linkml.yaml")))
    assert narrowed.all_classes(), "the restricted view still shows a class"
    for c in narrowed.all_classes():
        assert HIDDEN not in narrowed.class_slots(c), c
    assert HIDDEN not in narrowed.all_slots()
    schema = json.loads(view_restricted.path("model.schema.json").read_text())
    for name, definition in schema["definitions"].items():
        assert HIDDEN not in definition["properties"], name
    assert HIDDEN not in json.loads(view_restricted.path("context.jsonld").read_text())["@context"]
    names = {str(n) for n in rdflib.Graph().parse(view_restricted.path("model.shacl.ttl")).objects(None, SH.name)}
    assert HIDDEN not in names, names
    # whether anything was left out, never what (the platform's EP-47)
    assert view_restricted.model.get("redacted") is True
