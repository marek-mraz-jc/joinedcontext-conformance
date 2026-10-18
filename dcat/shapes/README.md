# DCAT-AP shapes, vendored (T-4510)

The SHACL files `tests/ckan/profiles.py` validates every Endpoint record against. Each official
file is the upstream file byte for byte, pinned by sha256 in `profiles.py` (`DIGESTS`); a test
fails when a file is not its pinned digest. Updating a file is a reviewed commit that changes the
file, this table and `DIGESTS` together. Nothing is fetched at test time.

Retrieved 2026-10-17.

| File | Source | Version | sha256 | Licence |
|---|---|---|---|---|
| `dcat-ap/3.0.0/dcat-ap-SHACL.ttl` | github.com/SEMICeu/DCAT-AP `releases/3.0.0/shacl/dcat-ap-SHACL.ttl` at `4470b8ea907b7eb9ec55ab22befd1ecd2210ff32` | DCAT-AP 3.0.0 | `92f76609d78d257123e75bc6b7155df5cc0a63f14c29fbc12b0ac95c56af2059` (the platform's pin) | CC-BY-4.0, © SEMIC |
| `dcat-ap-sk/3.0.0/dcat-ap-sk-3.0.0-shapes.ttl` | github.com/slovak-egov/centralny-model-udajov `tbox/national/dcat-ap-sk/dcat-ap-sk-3.0.0-shapes.ttl` at `15b09176edacf3fd2ba21612c1b8d950484fc076` (retrieved 2026-10-18) | DCAT-AP-SK 3.0.0 | `e2d2c01e4e3966af003121db41b378018241fcd1cd40a459cbbeb4c128caf31c` | Slovak public data standard, published by the state for reuse |
| `dcat-ap-sk/3.0.0/dcat-ap-sk-3.0.0-hvd-shapes.ttl` | the same folder and commit | DCAT-AP-SK 3.0.0, HVD | `b7774e877d19870c442563bd28a7ad85720318227c602299142a40a73d21eb2c` | as above |
| `dcat-ap-cz/2026-09-23/otevrena-data/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-otevřená-data/2026-09-23/shacl.ttl | OFN 2026-09-23 | `3c21e6977a2199e44a7dd6f594311d453f444687f32fe84707ab3c9c4069adc2` | Czech open formal norm (OFN), published by the state for reuse |
| `dcat-ap-cz/2026-09-23/hvd/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-hvd/2026-09-23/shacl.ttl | OFN 2026-09-23 | `aa4a4640f60d62c040ffe5a65874d0b388d1ca95c330a31710c9372d7c46d915` | as above |
| `dcat-ap-cz/2026-09-23/rozhrani-katalogu-dat/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-rozhraní-katalogů-dat/2026-09-23/shacl.ttl | OFN 2026-09-23 | `7aba774c0519534a46b15bf48c2942f84cac20ef51b2403f0fe882ecd1d61ff6` | as above |

Written here, not upstream files, so not pinned:

- `dcat-ap-cz/2026-09-23/cz-additions.shacl.ttl`: the cardinalities of the Czech tables. The
  published OFN `shacl.ttl` files carry types and value classes but no `sh:minCount` (except
  `hvdCategory`); the mandatory `[1..]` rows of each specification's tables are written here,
  each with an `rdfs:seeAlso` to its row IRI.

DCAT-AP-SK 3.0.0 publishes its own SHACL, so the 2.0.1 additions written here and the DCAT-AP
2.1.1 base they extended are gone (T-4586). Its `catalog_dataset` shape says `sh:maxCount 1`
against its own example catalogue of three datasets; `profiles.py` reports that row apart
(`UPSTREAM`). The HVD and catalogue-interface shapes are vendored for T-4499 and not in the
report yet. What each profile requires and where
the official text says so: `docs/API/dcat-profiles.md` in joinedcontext-docs.
