# DCAT-AP shapes, vendored (T-4510)

The SHACL files `tests/ckan/profiles.py` validates every Endpoint record against. Each official
file is the upstream file byte for byte, pinned by sha256 in `profiles.py` (`DIGESTS`); a test
fails when a file is not its pinned digest. Updating a file is a reviewed commit that changes the
file, this table and `DIGESTS` together. Nothing is fetched at test time.

Retrieved 2026-10-17.

| File | Source | Version | sha256 | Licence |
|---|---|---|---|---|
| `dcat-ap/3.0.0/dcat-ap-SHACL.ttl` | github.com/SEMICeu/DCAT-AP `releases/3.0.0/shacl/dcat-ap-SHACL.ttl` at `4470b8ea907b7eb9ec55ab22befd1ecd2210ff32` | DCAT-AP 3.0.0 | `92f76609d78d257123e75bc6b7155df5cc0a63f14c29fbc12b0ac95c56af2059` (the platform's pin) | CC-BY-4.0, © SEMIC |
| `dcat-ap/2.1.1/dcat-ap_2.1.1_shacl_shapes.ttl` | github.com/SEMICeu/DCAT-AP `releases/2.1.1/dcat-ap_2.1.1_shacl_shapes.ttl` at the same commit | DCAT-AP 2.1.1 | `467ace005196e261b9299422e921aba079c279a9e914e3a2d499e048cbc7cd0b` | CC-BY-4.0, © SEMIC |
| `dcat-ap-cz/2026-09-23/otevrena-data/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-otevřená-data/2026-09-23/shacl.ttl | OFN 2026-09-23 | `3c21e6977a2199e44a7dd6f594311d453f444687f32fe84707ab3c9c4069adc2` | Czech open formal norm (OFN), published by the state for reuse |
| `dcat-ap-cz/2026-09-23/hvd/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-hvd/2026-09-23/shacl.ttl | OFN 2026-09-23 | `aa4a4640f60d62c040ffe5a65874d0b388d1ca95c330a31710c9372d7c46d915` | as above |
| `dcat-ap-cz/2026-09-23/rozhrani-katalogu-dat/shacl.ttl` | https://ofn.gov.cz/dcat-ap-cz-rozhraní-katalogů-dat/2026-09-23/shacl.ttl | OFN 2026-09-23 | `7aba774c0519534a46b15bf48c2942f84cac20ef51b2403f0fe882ecd1d61ff6` | as above |

Written here, not upstream files, so not pinned:

- `dcat-ap-sk/2.0.1/sk-additions.shacl.ttl`: the rules DCAT-AP-SK 2.0.1 adds to DCAT-AP 2.1.1,
  one shape per section of the specification (github.com/datova-kancelaria/dcat-ap-sk-2.0 at
  `ef7713876fdb4f319f0ae692e306b55d03b2410e`, `index.bs`), each with an `rdfs:seeAlso` to it.
  The Slovak profile publishes no SHACL of its own: the repository holds the Bikeshed source
  and examples only, so the profile is DCAT-AP 2.1.1's official shapes plus this file.
- `dcat-ap-cz/2026-09-23/cz-additions.shacl.ttl`: the cardinalities of the Czech tables. The
  published OFN `shacl.ttl` files carry types and value classes but no `sh:minCount` (except
  `hvdCategory`); the mandatory `[1..]` rows of each specification's tables are written here,
  each with an `rdfs:seeAlso` to its row IRI.

The 2.1.1 shapes are only the base the Slovak profile extends; the HVD and catalogue-interface
shapes are vendored for T-4499 and not in the report yet. What each profile requires and where
the official text says so: `docs/API/dcat-profiles.md` in joinedcontext-docs.
