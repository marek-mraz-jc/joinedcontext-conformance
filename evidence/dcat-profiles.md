# DCAT-AP profiles: what each Endpoint record lacks (T-4510)

Written by `python3 tests/ckan/profiles.py --write evidence/dcat-profiles.md`; `tests/ckan/test_profiles.py` holds it equal to a fresh run. Records: the gateway's fixtures in `tests/ckan/fixtures/catalog-*.jsonld`. Shapes: `dcat/shapes/` (README there).

## DCAT-AP 3.0.0 (gate): conforms

### catalog-organization-cc-by-sa.jsonld: 0

### catalog-public-cc0.jsonld: 0

### catalog-restricted-bare.jsonld: 0

## DCAT-AP-SK 2.0.1 (reported, gate from T-4499): 59 violation(s)

### catalog-organization-cc-by-sa.jsonld: 20

| n | focus | path | message |
|---|---|---|---|
| 2 | `[blank]` | `spdx:algorithm` | does not contain a value in the set: ['spdx:checksumAlgorithm_sha1'] |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:distribution` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:keyword` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:theme` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:publisher` | Less than 1 values |

### catalog-public-cc0.jsonld: 20

| n | focus | path | message |
|---|---|---|---|
| 2 | `[blank]` | `spdx:algorithm` | does not contain a value in the set: ['spdx:checksumAlgorithm_sha1'] |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:distribution` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:keyword` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:theme` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:publisher` | Less than 1 values |

### catalog-restricted-bare.jsonld: 19

| n | focus | path | message |
|---|---|---|---|
| 2 | `[blank]` | `spdx:algorithm` | does not contain a value in the set: ['spdx:checksumAlgorithm_sha1'] |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dcat:keyword` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dcat:theme` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dct:publisher` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dcat:byteSize` | Value is not Literal with datatype xsd:decimal |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `leg:termsOfUse` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `leg:termsOfUse` | Less than 1 values |

## DCAT-AP-CZ 2026-09-23 (reported, gate from T-4499): 125 violation(s)

### catalog-organization-cc-by-sa.jsonld: 42

| n | focus | path | message |
|---|---|---|---|
| 1 | `[blank]` | `vcard:fn` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `dcat:endpointDescription` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:keyword` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:theme` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:publisher` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:spatial` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `r5r:applicableLegislation` | Less than 1 values |

### catalog-public-cc0.jsonld: 42

| n | focus | path | message |
|---|---|---|---|
| 1 | `[blank]` | `vcard:fn` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `dcat:endpointDescription` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:keyword` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dcat:theme` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:publisher` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `dct:spatial` | Less than 1 values |
| 1 | `https://opendata.example.org/dataset/events` | `r5r:applicableLegislation` | Less than 1 values |

### catalog-restricted-bare.jsonld: 41

| n | focus | path | message |
|---|---|---|---|
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dcat:keyword` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dcat:theme` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dct:accrualPeriodicity` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dct:publisher` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `dct:spatial` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `dcat:endpointDescription` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f#service` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.csv` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.geojson` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.xlsx` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/file.zip` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/mcp` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ngsi-ld/v1/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/ogc/features/` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.schema.json` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/schema/v1/model.shacl.ttl` | `r5r:applicableLegislation` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `dct:title` | Value is not Literal with datatype rdf:langString |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `pu:specifikace` | Less than 1 values |
| 1 | `https://data.example.org/api/endpoint/k4y7pq2mzt6vhx3nbwrs5cjd8f/sta/v1.1/` | `r5r:applicableLegislation` | Less than 1 values |
