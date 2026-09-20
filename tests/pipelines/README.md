# Pipeline golden tests (TS-16, PL-22)

`bento test` over the transformation pipelines: the same input batch must always produce the
same NGSI-LD output (PL-38). A pipeline whose mapping drifts fails here before it reaches a
context space.

Task: T-0077.

## Run

```bash
PIPELINES_DIR=/path/to/projects jc-conformance pipelines
```

Every `*_bento_test.yaml` (Bento's own convention), `tests.yaml` or `test_definition.yaml` under
`PIPELINES_DIR` is executed. Finding none is an error, not a pass — `run.sh` exits 2.

## Where bento comes from

Bento is **not** installed in the conformance runner image. Its binary carries HIGH and CRITICAL
advisories from its own module pins (`x/crypto` ssh authentication bypass, `grpc`, `thrift`,
`amqp091`), and an image we sign and publish does not get to carry those — the alternative would
have been an ignore entry per advisory, which is weakening a control, not fixing it.

Take the binary from the pinned upstream image instead, next to wherever you run the suite:

```bash
cid=$(docker create ghcr.io/warpstreamlabs/bento:1.21.1@sha256:656c55de3f8deddd4ee743f3c76f3b497e67324e940f2bc1769693cd8b906364)
docker cp "$cid:/bento" ./bento && docker rm "$cid"
PATH="$PWD:$PATH" PIPELINES_DIR=./projects tests/pipelines/run.sh
```

`run.sh` exits 2 with that hint when `bento` is not on PATH. The `ci` lane does exactly this.

## Fixtures and self-test

`fixtures/parking-mqtt/` is a vendor-telemetry-to-ParkingSpot mapping with its golden tests, used
by `selftest.sh`: the harness must pass on the fixture, must fail when the mapping stops matching
the golden output, and must exit 2 when it finds no test definition at all.

```bash
tests/pipelines/selftest.sh
```
