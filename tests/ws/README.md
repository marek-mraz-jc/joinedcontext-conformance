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
| `test_a_mapping_of_another_ir_version_is_refused` (400, IR version, DM-52) | views |
| `test_a_connection_ends_at_its_max_lifetime` (`lifetime`, 1000) | 8 |

`test_rfc6455.py` runs `rfc6455_probe.py`: the Autobahn sections that need no echo server (ping and
pong, reserved bits and opcodes, fragmented control frames, the close handshake, close codes,
unmasked frames), judged on control frames only, because the channel sends its own `welcome` and
the Autobahn fuzzingclient marks every case FAILED against it (T-4879). It also runs alone against a
deployed Endpoint: `WS_PROTOCOL=ngsi-ld-ws python3 tests/ws/rfc6455_probe.py wss://api.{domain}/api/endpoint/{slug}/ws`.

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

## The fan-out smoke on dev (EP-115, T-4133)

`fanout_smoke.py` holds ten anonymous sockets on dev's transport Endpoint with two filters each,
one of every kind the gateway's fan-out serves (type, idPattern, q, geoQ, watchedAttributes,
notification.attributes, throttling), for 60 s. Every filter must notify and nothing outside it
may arrive; a gap, a close or a refused subscription fails it. Stdlib only.

```sh
python3 tests/ws/fanout_smoke.py wss://api.dev.joinedcontext.com/api/endpoint/ajaezssvx3bwqgejgepmq4ig3frgan4e/ws
```

## Isolation through the edge on dev (T-4909, T-4872 step 3, T-4874)

e2e journey 48 (`e2e/journeys/48-websocket-isolation.spec.ts`, workflow `ws-isolation.yml`, on
demand) proposes two sandbox spaces in `helsinki`: the first with a public Endpoint hiding `cause`
and `alertSource` and an organization Endpoint hiding `cause`, the second with the same entity ids.
Read Policies name only `Alert`, so `GtfsStop` is the type no Endpoint shows. The conformance
account (`banskabystrica-conformance`, whose token names the two fixed slugs in deployment
`components/context-gateway/keycloak-clients.yaml`) writes for 30 minutes: Alert creates, shown
updates, hidden-only updates, Alerts whose `refStop` names a canary GtfsStop, GtfsStop writes and
the second space's copies. Sockets stay open on both Endpoints (a token socket is reopened at its
`exp`), each with a plain, a `join=inline` and a canary-type subscription, and a REST read with
`join=inline` runs each round. `e2e/ws-isolation/verdict.ts` fails the run on any canary marker,
canary entity, hidden attribute, authenticated-only attribute on the anonymous side, notification
after a hidden-only update, or frame after `x-closing`; `verdict.spec.ts` proves each goes red,
offline, in `ci.yml`. At the end the public Endpoint is narrowed to `organization`: its socket gets
`x-closing` `endpoint-changed` and closes 1000, and a new anonymous upgrade answers 401. The
journey then deletes everything it proposed.

```sh
PORTAL_USER=… PORTAL_PASSWORD=… PORTAL_APPROVER_USER=… PORTAL_APPROVER_PASSWORD=… \
NGSILD_CLIENT_SECRET=… JC_WSISO_MINUTES=30 \
  ./node_modules/.bin/playwright test --config e2e/playwright.config.ts journeys/48-websocket-isolation.spec.ts
```
