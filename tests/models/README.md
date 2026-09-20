# LinkML artifact conformance suite (T-0078: TS-18, DM-02, DM-03, DM-43, DM-46)

Validates that every `kind: DataModel` manifest in a projects tree has consistent,
valid artifacts and that the served SHACL shapes correctly accept the example entity
and reject invalid payloads.

## What the suite proves

- **DM-01 / DM-02**: LinkML source is authoritative; generated artifacts are committed
  beside it (`json-schema/{name}.v{major}.json`, `context/{name}.v{major}.jsonld`,
  `docs/{name}.md`, `examples/{name}.example.jsonld`).
- **DM-03**: The committed JSON Schema dialect is draft-07. No 2019-09 or 2020-12
  keywords (`$defs`, `unevaluatedProperties`, `prefixItems`, etc.) are emitted.
- **DM-22**: Semantic version major matches the `v{major}` component in artifact paths.
- **DM-26**: Model lifecycle is one of `draft`, `published`, `deprecated`, `retired`.
- **DM-43 / DM-46**: The served SHACL shapes validate the committed example entity
  expanded with the served `@context` via pySHACL, and reject entities violating the model.
- **TS-18**: The committed example entity validates against the committed JSON Schema.

## Environment variables

- `PROJECTS_DIR`: Path to the directory containing project spaces and `kind: DataModel`
  manifests. Required when running `run.sh` or pytest directly.
- `MODEL_TOOLS_CMD`: Optional command invoking Model Tools generator (e.g.
  `jcctl model generate`). Model Tools lives in the platform repository (`tools/model-tools/`)
  and is deliberately not packaged in this conformance runner image. When unset,
  regeneration diff tests skip cleanly instead of pretending.
- `JC_REPORTS_DIR`: Output directory for JUnit XML and test logs. Defaults to
  `tests/models/reports`.

## Run

```bash
PROJECTS_DIR=/path/to/projects jc-conformance models
# Or directly via bash:
PROJECTS_DIR=/path/to/projects ./tests/models/run.sh
```

## Self-test

Proves that the harness can go red on negative cases (drifted version, forbidden keywords,
lenient shapes, broken entities, missing manifests, regeneration mismatches):

```bash
python3 tests/models/selftest.py
```

## Fixtures

`fixtures/bb-ovzdusie/spaces/ovzdusie/datamodels/` contains a reference `AirQualityObserved`
model with consistent LinkML source, JSON Schema draft-07, JSON-LD `@context`, SHACL shapes,
Markdown docs, and example entity.

---

# LinkML Mapping parity conformance suite (T-0079: DM-35, DM-39, DM-52)

Validates that every `kind: Mapping` manifest in a projects tree has committed compiled
artifacts (Bloblang and gateway IR), carries golden tests, and that the compiled Bloblang,
gateway IR interpreter, and Model Tools produce identical results across golden test cases.

## What the suite proves

- **DM-35**: Model Tools compiles every Mapping to Bloblang committed at
  `generated/{name}.blobl` beside the specification.
- **DM-39**: Every Mapping carries at least one golden test (`spec.tests[]`) executed
  across engines with identical results.
- **DM-52**: Model Tools compiles every Mapping to gateway mapping IR committed at
  `generated/{name}.ir.json`; golden tests must pass identically through Bento, the gateway
  IR interpreter, and Model Tools without divergence. A parity check with fewer than two
  engines fails.

## Environment variables

- `BENTO_ENGINE_CMD`: Optional command executing Bento over the compiled Bloblang artifact.
- `GATEWAY_IR_ENGINE_CMD`: Optional command executing the Context Gateway IR interpreter over
  the compiled IR artifact.
- `MODEL_TOOLS_ENGINE_CMD`: Optional command executing Model Tools specification directly.
- `MODEL_TOOLS_CMD`: Optional command invoking `jcctl model` for artifact recompilation tests.

When no engine is configured, engine execution tests skip cleanly naming the variables. When
only one engine is configured, the parity check fails because parity requires at least two
engines to agree.

None of the three engines is in the runner image: Bento is kept out for the advisories its binary
carries (see `tests/pipelines/README.md`), and the IR interpreter and Model Tools are built in the
platform repository. The fast `ci` lane therefore runs the two self-tests, not the suite: there is
no projects tree in this repository to point `PROJECTS_DIR` at, and a suite that passes because it
found nothing to check is worth nothing. Point it at a checkout of the configuration repository
when you have one.

## Self-test

Proves that the harness goes red on negative cases (single engine, divergent engine, edited
expect file, missing golden tests, missing IR artifact, unconfigured engines):

```bash
python3 tests/models/selftest_parity.py
```

## Fixtures

`fixtures/bb-ovzdusie/spaces/ovzdusie/mappings/` contains a reference `sdm-airquality-to-bb`
mapping with its golden tests, committed `.blobl` and `.ir.json` artifacts, and stub engines.
