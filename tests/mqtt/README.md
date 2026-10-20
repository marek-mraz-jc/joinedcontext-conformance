# MQTT edge broker interoperability (T-4879)

The Eclipse Paho interoperability tests (`eclipse-paho/paho.mqtt.testing`, `interoperability/client_test.py`
for MQTT 3.1.1 and `client_test5.py` for MQTT 5, pinned by commit in `run.sh`) against RMQTT as
`joinedcontext-deployment` ships it: the image digest of `components/mqtt-brokers/images.yaml` and the
chart's listener in `rmqtt.toml`, with anonymous clients and no auth or ACL plugin, because the
gateway that answers CONNECT on a cluster is not here.

`baseline.json` holds the outcome of every case on that configuration (2026-10-20: 3.1.1 5 of 10,
MQTT 5 16 of 27). `paho_interop.py check` goes red when a case the baseline passes fails or does not
run, and lists a case that starts passing so the baseline can be ratcheted. Each case that waits
more than 60 s counts as `TIMEOUT`.

The cases the baseline fails, and why:

| Cases | Cause |
|---|---|
| `test_retained_messages`, `test_retained_message` | no retainer plugin: the broker keeps nothing (ADR-N-048) |
| `test_offline_message_queueing`, `test_redelivery_on_reconnect` (3.1.1) | `session_expiry_interval = "0s"`: sessions end with the connection |
| `test_overlapping_subscriptions`, `test_flow_control2` | `max_qos_allowed = 1`: the cases publish and subscribe at QoS 2 |
| `test_subscribe_failure` | the case needs a broker that refuses the filter `test/nosubscribe`; on a cluster the gateway's ACL does that |
| `test_server_keep_alive`, `test_client_topic_alias`, `test_server_topic_alias`, `test_payload_format`, `test_user_properties`, `test_subscribe_options`, `test_shared_subscriptions` (5) | RMQTT 0.24.0 with this listener; not features the platform's devices or the pipeline runner use today |

```sh
tests/mqtt/run.sh                                  # starts the broker in docker
MQTT_HOST=127.0.0.1 MQTT_PORT=1883 tests/mqtt/run.sh   # a broker that is already up
python3 tests/mqtt/paho_interop.py --selftest      # the gate goes red on a planted regression
```

A dev broker needs a CONNECT the gateway admits (a device token as the password), which the Paho
cases do not send; the dev run waits for T-4857.
