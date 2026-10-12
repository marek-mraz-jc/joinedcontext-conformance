# Helsinki live feed, end to end

`live.py` (T-3907, AP-147) checks that the Helsinki buses on the platform follow HSL's
high-frequency positioning feed. For five minutes it listens to the four trunk-line topics of the
`hsl-hfp` DataSource on `wss://mqtt.hsl.fi` (public, read only), reads the transport endpoint's
Vehicle entities every 15 s and holds one Subscription on the endpoint's `/ws`. It then matches
them bus by bus, joining each frame's `tst` to the `observedAt` the pipeline writes.

| line | red when |
|---|---|
| `position` | a served position is more than 50 m from the frame with the same `tst` |
| `speed` | the speed differs from the frame's `spd`, or its unitCode is not `MTS` |
| `route` | the route differs from the frame's route id (`2550` is line 550) |
| `presence` | a bus served with an `observedAt` inside the run sent no frame during it |
| `freshness` | a served bus is older than the 300 s slot ttl, or 30 s behind its own newest frame |
| `completeness` | under 95 % of an admitted bus's frames (from its first served position on) reached the broker |
| `latency` | p95 of broker `modifiedAt` → WebSocket receive is 1 s or more; p50/p95 of `tst` → `modifiedAt` and `tst` → receive are printed beside it |
| `no data` | the feed delivered no frame or the endpoint served no Vehicle |
| `run` | a read, the subscribe or the socket failed |

The report prints its raw counts. Exit 0 only when every line is green. A run without data is
red, never green. The token travels in the Authorization header only. The Subscription lives
as long as the socket and is the only state the run creates.

```sh
just helsinki-live                        # dev profile, five minutes
python3 -m pytest tests/helsinki-live -q  # the matcher on fixtures, no network
```

It is part of the hourly schedule of `scripts/scheduled-dev.py`.
