# WebSocket notification channel of a view

The suite Antares `docs/analysis/surfaces-conformance.md` section 6 defines (T-3518), run by the
Antares workflow `surfaces-conformance.yml` (job `ws-conformance`) against the `full` build with
the `ws` and `ws-views` surfaces, loaded by its `dev/views-rig.sh`. No ETSI binding of the channel
exists; the contract is the broker's `docs/api/asyncapi-ws.yaml` (ADR-0026 rule 11), and every
message the broker sends in these sessions is validated against its message schemas.

| case | ADR-0026 rule |
|---|---|
| `test_welcome_names_the_connection_and_its_resume_token` | 4, 7 |
| `test_subscribe_mints_the_endpoint_and_the_subscription_reads_over_http` | 4 |
| `test_a_subscription_naming_another_endpoint_is_refused` | 4 |
| `test_two_notifications_carry_the_body_the_same_subscription_delivers_over_http` | 3 |
| `test_update_and_unsubscribe_answer_as_http_would` | 5 |
| `test_resume_delivers_what_followed_the_last_seq` (coalesced while away) | 6, 7 |
| `test_resume_past_the_ring_starts_with_a_gap` | 7 |
| `test_resume_with_a_wrong_token_is_not_found` | 7 |
| `test_a_removed_view_closes_its_connections` (`view-removed`, 1008) | views |
| `test_a_connection_ends_at_its_max_lifetime` (`lifetime`, 1000) | 8 |

The closing reasons `overflow` and `idle` need a slow socket or a minute of silence; the crate's
own tests cover them (`ws_a_slow_consumer_is_disconnected_not_buffered`, the idle bound).

```sh
npx --yes @asyncapi/cli@2.16.0 validate "$ANTARES/docs/api/asyncapi-ws.yaml"
WS_URL=ws://localhost:8080/x/views/all/ws \
ASYNCAPI_DOC="$ANTARES/docs/api/asyncapi-ws.yaml" \
WS_QUEUE=4 \
  tests/ws/run.sh
```

The broker runs with `ANTARES_API_SURFACES=admin,views,ws,ws-views`, `ANTARES_WS_QUEUE` equal to
`WS_QUEUE` (the resume ring the gap case overruns), `ANTARES_WS_MAX_LIFETIME_SECS=45` (past the
30 s heartbeat that notices a removed view, before the 60 s idle close) and
`ANTARES_EGRESS_ALLOW_PRIVATE=true`, so it can POST the HTTP twin of a notification to the suite's
loopback receiver (`WS_RECEIVER_HOST`, default `127.0.0.1`). A run takes about 80 s, most of it
the lifetime and view-removed waits.
