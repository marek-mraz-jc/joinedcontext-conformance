# OData conformance of a view's endpoint

The suite Antares `docs/analysis/surfaces-conformance.md` section 4 defines (T-3510), run by the
Antares workflow `surfaces-conformance.yml` against the `odata` build loaded by its
`dev/views-rig.sh`.

| file | cases |
|---|---|
| `test_abnf.py` | `test_abnf_cases_live`: the OASIS ABNF test cases of the URL rules, live |
| `test_metadata.py` | `$metadata` as XML against `edmx.xsd` (xmllint) and as JSON against `csdl.schema.json` (jsonschema-rs) |
| `test_parity.py` | `test_filter_parity_with_q`: `$filter` and NGSI-LD `q` answer the same ids, under view `all` and `parity-restricted`; the rejected rows 501 |
| `test_writes.py` | Capabilities restrictions obeyed, `$expand` skips out-of-view targets, deep insert, bind and `$ref` check the linked Entity |

```sh
ODATA_URL=http://localhost:8080/x/views/all/odata/v4 \
ODATA_RESTRICTED_URL=http://localhost:8080/x/views/parity-restricted/odata/v4 \
  tests/odata/run.sh                       # everything
  tests/odata/run.sh -k "abnf or metadata" # the merge-request subset
```

Two client checks read `$metadata` and one page of view `all`, then the restricted view and fail
when its model holds `NO2`, the member that view hides (ADR-0024 decision 7):

```sh
clients/dotnet/run.sh   # Microsoft.OData.Client 9.0.0, .NET 10 SDK
clients/sap/run.sh      # SAP Cloud SDK generator and odata-v4 4.9.1, Node 22
```

The service root is absolute (`ANTARES_PUBLIC_URL`, else the request's `Host`): Microsoft.OData.Core
refuses a payload whose context URL is a path (Antares T-3688).

`ODATA_PINS_DIR` keeps the OASIS files between runs; they are fetched at their pinned commits
and checked against their SHA-256, never committed (OASIS IPR policy). The XML case needs
`xmllint` (Debian `libxml2-utils`).
