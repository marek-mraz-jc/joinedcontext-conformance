# NGSI-LD read smoke

Reads what a deployed Endpoint holds, anonymous, and writes nothing (ETSI GS CIM 009 V1.9.1 clauses
in each test name): the type list, a query by type, retrieval by id, 404 ResourceNotFound, the
results count, the next link, BadRequestData for `limit=0` without `count` and for a query without
a selector, keyValues and GeoJSON. `tests/etsi/smoke.robot` writes its fixtures, so it runs only on
a dev write Endpoint; this suite is what prod gets.

```sh
NGSILD_URL=https://api.smartcity.joinedcontext.com/api/endpoint/{slug}/ngsi-ld/v1 tests/ngsild-read/run.sh
```
