# LinkML surface of a view

The suite Antares `docs/analysis/surfaces-conformance.md` section 5 defines (T-3512), run by the
Antares workflow `surfaces-conformance.yml` (job `linkml-conformance`) against the `full,linkml`
build loaded by its `dev/views-rig.sh`. The judges are the reference tools, pinned in
`requirements.txt`: `linkml` 1.12.0 (`linkml-lint`, `gen-json-schema`, `gen-shacl`, `SchemaView`)
and `pyshacl` 0.40.1.

| case | views | check |
|---|---|---|
| `test_served_schema_loads` | both | `model.linkml.yaml` loads in `SchemaView` with the classes the catalogue names, and `linkml-lint` reports no error |
| `test_reference_generators_accept_the_schema` | both | `gen-json-schema` and `gen-shacl` run on it and describe every class |
| `test_served_json_schema_validates_the_view` | `all` | every Entity of the view in keyValues validates against `model.schema.json` |
| `test_served_shacl_validates_the_view` | `all` | the same Entities, read through `context.jsonld`, conform to `model.shacl.ttl` (and the shapes to SHACL-SHACL) |
| `test_hidden_attributes_are_absent_from_the_schema` | `parity-restricted` | `NO2`, which view `all` shows, is in no class, property, term or shape of the narrowed documents |

The data checks judge the served JSON Schema and SHACL, not what the LinkML generators make of the
schema: LinkML inlines a class range without an identifier, while an NGSI-LD Relationship holds
the target's id, and a GeoProperty's LinkML range is the default `string`; only the broker's
`ngsi_ld_kind` annotation says it is a geometry. The SHACL check runs on view `all`, whose
Entities include every Relationship target, so `sh:class` can hold.

```sh
LINKML_URL=http://localhost:8080/x/views/all/linkml \
LINKML_RESTRICTED_URL=http://localhost:8080/x/views/parity-restricted/linkml \
  tests/linkml/run.sh
```
