---
sidebar_position: 1
title: Production readiness
description: The privilege-escalation review (T-3588) and the final code-graph sweeps (T-3590 to T-3592), path by path, with the query, the code read, the attack test and the verdict.
---

# Production readiness

This page decides whether the joinedcontext platform can go to production. For every way a caller could get from a right it holds to data or rights it was not granted, it gives the code-graph query that finds every way in, the code read behind it, the attack test that plays it, and a verdict. The production gate is zero open priority-1 findings. Security reviewers and the owner read it. Exploit detail stays here, in the private conformance repository.

Reviewed commits: joinedcontext-platform `40a184b` (main), joinedcontext-portal `e58601a` (main), joinedcontext-deployment main of 2026-10-17, Antares-NGSI-LD-Context-Broker `b7200437` (dev). The broker's own threat model (Antares `docs/analysis/surfaces-threat-model.md` and `docs/analysis/threat-model/*.md`, T-3447) is the baseline. This review checks the named tests against the tree and walks the paths that cross the gateway, the Portal and the App services, which that model does not cover.

## 1. How to read and rerun this page

Each path is one table: the attack, the cg query or GREP that lists the way in, where the code was read, the test that plays it (✚ marks one this review added), and the verdict. A verdict is `holds` (the attack fails, and a test proves it), `finding F<n>` (a priority-1 task in group `antares-hardening`, with its failing test), or `open` (not walked yet, with what is missing).

The queries run against a fresh graph of the commit above:

```bash
cd joinedcontext-platform && cg build --reuse && cg q -e "<query>"
cd Antares-NGSI-LD-Context-Broker && cg build && cg q -e "<query>"
```

## 2. Findings

| Id | Path | What | Task | State |
|---|---|---|---|---|
| F1 | 1 | The gateway authorized a dot segment as a name and forwarded it: `PATCH …/entities/{id}/attrs/..` is decided as Update Attributes of one attribute, and a hop that resolves dot segments (RFC 3986 clause 5.2.4) runs it as every attribute of the body. Not exploitable against Antares, which refuses a dot segment in an id or attribute name (`antares-model/src/lib.rs:267,290`, `src/id.rs:193`). It is a parser differential all the same. | T-4389 | fixed, merged to platform main (`8009039`) |
| F2 | 3 | An App's page can plant session cookies for the Portal and every other App: the App origins and the Portal are the same site, every session cookie is unprefixed, and a planted one wins whenever the victim has none at the edge, and always in the Portal's own jar. Login CSRF: the victim works inside the attacker's session. Priority 2 (Decided, worker-3: no right is gained directly, the victim must act inside the planted session). | T-4395 | in review |
| F3 | 9 | Antares: an offset near `i64::MAX` under conditional view rules wrapped into a negative SQL LIMIT, so Postgres answered 500. No data and no right; section 9.3. | T-3590 | fixed on Antares `agent/worker-3/T-3588`, in review |
| F4 | 10 | Platform: three token clients followed redirects, so a redirecting token endpoint would have received the credential form again at another host. Needs a compromised realm; section 10.3. | T-3591 | fixed on platform `agent/worker-3/T-3591`, in review |
| F5 | 11 | Portal: the three Keycloak admin clients followed redirects, so a redirecting realm token endpoint would have received the client secret again at another host. Needs a compromised realm; section 11.3. | T-3592 | fixed on portal `agent/worker-3/T-3591` (`43911cc`), in review |
| T-2479 | 5 | The Portal's Role may read every Secret of its instance (existing task, not found here). | T-2479 | blocked, priority 3 |

Reference checks against the broker's threat model (every test name in a row's test column, looked up in the tree at `b7200437`):

| Row | Named test | In the tree | Verdict |
|---|---|---|---|
| odata O2 | fuzz target `odata_filter_to_qnode` (proposed) | no; `fuzz/fuzz_targets/odata_url_parse.rs` fuzzes the parser, not the translation | the property test `filter_props.rs::odata_filter_translation_agrees_with_reference_eval` covers the translation; the fuzz target stays a T-3590 item, not priority 1 |
| odata O6 | `odata_expand_levels_above_max_is_400`, `odata_expand_max_is_clamped_to_max_levels` (proposed) | no | holds: `$levels` and nested `$expand` still answer 501 (`odata_expand.rs::odata_expand_beyond_one_level_is_501`), so the bound has nothing to clamp yet |
| odata O23 | `ops_seam.rs::a_change_event_names_the_surface_and_the_view` (proposed) | no | stale reference: T-3593 resolved G10 with the `surface operation` log line (`ops_log.rs::a_surface_operation_is_logged_with_the_surface_and_the_view`); T-3590 corrected the row |
| mcp MC16 | `join_is_not_an_argument_until_the_target_is_checked` | renamed | holds: the row names the rename to `mcp_gate.rs::join_is_an_argument_now_that_the_target_is_checked` |

Every other test a row names exists in the tree (check: every backticked name of 15 or more characters in a row's test column, against the `fn` names under `crates/`, `tests/`, `addons/` and `fuzz/`).

## 3. Path 1: an Endpoint token to broker admin, another tenant or another view

The gateway holds the broker. A caller holds an Endpoint, which pins one Context Space (the broker Tenant) and one view. The way out of it would be a path, a header or an id the gateway forwards without deciding on it.

| Attack | Way in (cg) | Code read | Test | Verdict |
|---|---|---|---|---|
| A client path reaches a broker admin door (`/q/tenants`, `/q/dead-letters`, tenant purge) or the peer door `/ex/v1/remote-notify` (T-3569) | `GREP "/q/\|dead-letter\|remote-notify\|/ex/v1" IN crates/context-gateway/src/**`: 0 hits. Every forward goes through `Broker::send`: `MATCH (c:Symbol)-[r:CALLS]->(f:Symbol) WHERE f.path = 'context_gateway::proxy::Broker::send' AND NOT c.is_test RETURN c.path, r.line` (15 call sites, each building `/x/views/{view}/…` from the Endpoint's own view) | `app.rs:968-1070` (`serve_ngsi_ld_view`), `operations.rs:16` (`operation_of`, a closed table of CIM 009 clause 5 shapes, anything else 404) | ✚ `context-gateway/tests/attack_dot_segment_tests.rs::the_brokers_admin_registration_and_peer_doors_are_no_endpoint_operation` | holds |
| The same through dot segments (`/../../q/tenants`, `…/attrs/..`, `%2e%2e`) | as above, plus the client's rest of the path appended by `schema_routes.rs:107,158` and `ogc.rs:166` | `operations.rs`, `proxy.rs:267` | ✚ `attack_dot_segment_tests.rs::a_dot_segment_never_reaches_the_broker`, ✚ `edge_gateway_proxy_send_tests.rs::a_dot_segment_in_any_forwarded_path_is_refused_before_the_hop` | finding F1, fixed |
| A forged `NGSILD-Tenant` or subject header moves the request to another Tenant | `GREP "NGSILD-Tenant\|ngsild-tenant" IN crates/context-gateway/src/**`: `middleware/tenancy.rs:17,23` (the forgeable list), `pdp/conditional.rs:17` | `middleware/tenancy.rs` (strips every forgeable header, sets the Endpoint's space) | `tenancy_middleware_tests.rs`, `edge_gateway_tenancy_strip_tests.rs`, `attack_foreign_space_ids_tests.rs` (every write route with a forged tenant and foreign-space URNs lands in the pinned space) | holds |
| An entity id naming another space or organization carries a write out of the space | as above | `attack_foreign_space_ids_tests.rs` header (ADR-N-041: the space is the Endpoint's, never a URN segment) | `attack_foreign_space_ids_tests.rs` (4 URN shapes on every write route) | holds |
| A Context Source registration through an Endpoint makes the broker read another Tenant (the confused deputy) | `operation_of` has no `csourceRegistrations` row | `operations.rs:16-80` | ✚ `attack_dot_segment_tests.rs::the_brokers_admin_registration_and_peer_doors_are_no_endpoint_operation` (POST and GET `/csourceRegistrations`, POST `/csourceSubscriptions`: 4xx, no hop); in the broker, threat-model internal-csr C1 to C9 (`icsr_grant.rs`, `icsr_gate.rs`, `icsr_notify.rs`) | holds |
| Another Tenant's rows through a store path | broker: `dev/security.cypher` statements `store-method-without-tenant` and `tenant-not-from-header` (Antares `docs/analysis/tenant-isolation.md` §2-3; 17 methods and 6 routes classified) | Antares `antares-store` drivers | `antares_store::contract::run_tenant_isolation_contract` on memory, file and Postgres | holds |
| Another view: the caller names the view | the view comes from the Endpoint (`view(&endpoint)` in `serve_ngsi_ld_view`), never from the request | `app.rs:991` | `route_authz_sweep_tests.rs` (every route refuses whom the policy does not admit); broker V20 `a_view_id_is_matched_on_the_raw_segment` | holds |

## 4. Path 2: a view to hidden data

Since T-3644 and T-3648 the gateway no longer narrows NGSI-LD or MCP answers itself: it forwards to the broker's view, which conjoins the view and the engine before the store, paging, ordering and counting run (ADR-0023). Hidden data is the broker's to withhold, and the rows below are the broker's threat-model rows with their tests checked against the tree.

| Attack | Where it is decided | Test | Verdict |
|---|---|---|---|
| OData `$filter` on a hidden attribute or through a relationship path | ADR-0023 rule 2 on the translated AST (odata O2, views V1, V9) | `views_a_hidden_attribute_does_not_exist_for_q`, `q_over_hidden_equals_q_over_absent`, `a_linked_q_path_does_not_cross_into_an_out_of_view_entity`, `odata_filter_ne_doubling_is_checked_on_the_translated_tree` | holds |
| OData `$expand` to an out-of-view target | rule 6, one visibility function with `join` (odata O5, views V8) | `views_join_omits_out_of_view_targets_and_keeps_the_relationship`; `odata_expand.rs::odata_expand_beyond_one_level_is_501` (a name the view hides: 400) | holds |
| OData `$batch` members: one member judged under another's decision, a part header that changes tenant or subject, a part outside the service root | each member gated alone; part header allowlist (odata O9, O12) | `odata_batch.rs::odata_batch_members_are_gated_one_by_one`, `odata_batch_part_headers_cannot_change_tenant_or_subject`, `odata_batch_refuses_a_part_outside_this_service_root`, `odata_batch_member_is_refused_like_the_request_alone`, `odata_batch_content_id_reference_is_501` | holds |
| MCP tool arguments: `join`, `q`, ids | MCP rule 4 and the view (mcp MC16) | `mcp_gate.rs::join_is_an_argument_now_that_the_target_is_checked`; fuzz target `mcp_tool_arguments` | holds |
| LinkML schema lists hidden types, attributes or enum values | rendered from the intersected model (linkml LK1) | `linkml_view.rs::linkml_prefixes_and_context_hold_only_visible_terms`, `linkml_errors_carry_no_hidden_name`, `linkml_a_relationship_to_a_hidden_class_is_uriorcurie`, `linkml_a_denied_class_or_slot_is_the_404_of_an_absent_one` | holds |
| WebSocket notifications: `previousValue`, a change of a hidden attribute only, joined targets | rules 6 and 9 (views V14, V15, ws W5) | `views_notifications_drop_hidden_changes_and_strip_previous_value`, `views_subscriptions.rs::a_hidden_only_change_leaves_times_sent_unchanged`, `ws_delivery.rs::ws_notifications_pass_pre_notify_under_the_subscriber` | holds |
| A view-exit message (`x-viewExit`) tells the client a hidden value changed | no such message: ADR-0026 lines 170-174 reject it for that reason | n/a (the absence is the control) | holds |
| Counts and paging: `count=true&limit=0`, `$count`, `Link` pages | rule 1: the conjunction comes before paging and count (views V3, odata O3) | `views_count_and_paging_describe_the_narrowed_set`, `odata_count_is_the_narrowed_count` | holds |
| Telling a hidden entity from an absent one (400 against 404, timing) | rule 3: 404 ResourceNotFound, the answer an absent id gets (views V5, V2) | `views_an_out_of_view_entity_is_not_found`, `policy_filter_5_7.rs::a_hidden_entity_is_a_resource_not_found`, `tenant_isolation.rs::cross_tenant_probe_is_indistinguishable_from_nonexistence`, `views_q.rs::a_hidden_term_never_reaches_the_store`; live: `tests/security/test_silent_narrowing.py` | holds |
| What a gateway Endpoint serves across all its surfaces against what it may | the gateway's leak sweep | `context-gateway/tests/leak_sweep_tests.rs::no_surface_leaks_with_an_honest_broker` (and with a dishonest one) | holds |

## 5. Path 3: an App to more than it declared

An App calls the platform services on its own origin, `{name}.apps.{domain}` (`portal src/apps/static_host.rs:222` `app_origin`). Every service route runs `apps::services::gate` first (`src/apps/services.rs:29`): the request is on the App's own origin, the App is published and the caller may open it, a person is signed in, a write carries the CSRF token, and the service is on at the organization, the project and the App (AP-164). Portal graph at `e58601a`: `ROUTES apps;` lists 18 routes, and the 7 service routes all start with `services::gate`.

| Attack | Way in | Code read | Test | Verdict |
|---|---|---|---|---|
| A service the App did not declare or a layer switched off | `services::off_at` on every call | `services.rs:119` | `app_files_service_tests.rs::an_app_without_files_is_refused_naming_the_app`, `app_email_tests.rs::a_service_off_at_any_layer_is_refused_with_that_layer`, `app_jobs_service_tests.rs::an_app_without_the_service_or_a_caller_without_a_session_gets_no_list`, `app_ai_service_tests.rs::a_refused_key_is_the_installations_and_ai_off_never_reaches_the_proxy` | holds |
| Another App's files, jobs, email or AI through `/apps/{other}/api/services/…` | the `{name}` segment must be the request's own origin (`off_its_origin`, `static_host.rs:237`), else a 308 to that App's origin, where a cross-origin read needs CORS the Portal does not grant; object keys are under the App's prefix | `static_host.rs:237-282`, `files.rs:102` | `static_host_tests.rs::an_app_asked_for_on_the_portal_host_is_sent_to_the_apps_origin`, `app_files_service_tests.rs::a_key_outside_the_prefix_never_reaches_the_store` | holds |
| An App's page plants cookies for the Portal or another App (cookie tossing onto `{domain}`, the same site) | an App runs its own script (CSP `script-src 'self'`, no `sandbox`, `static_host.rs:601`), and every session cookie is host-only without the `__Host-` prefix: `jc_edge`, `jc_edge_apps` (deployment `components/portal/apisix-plugins.yaml:79,168,328,597`), `jc_session`, `jc_oidc_flow`, `jc_refresh` (`src/auth/session.rs:18-24`), `jc_csrf` (`src/auth/csrf.rs:14`) | axum_extra 0.12.6 `CookieJar::from_headers` keeps the last duplicate (`cookie` 0.18.2 `add_original` is `HashSet::replace`); the edge reads the first | the failing test ships with T-4395 | finding F2 |
| The AI proxy past its budget, or email past its quota, by calling in parallel | `services::take`: one conditional upsert (`… DO UPDATE … WHERE used + n <= limit`), atomic, and a reservation before the model is asked, settled after (T-3661) | `services.rs:173-226` | `app_ai_service_tests.rs::a_prompt_larger_than_the_day_left_is_the_quota_answer`, `app_email_tests.rs::the_send_past_the_quota_is_refused_with_429` | holds |
| Email as an open relay or with injected headers | recipients are people of the App, never a typed address; CR/LF refused | `src/apps/email.rs` | `app_email_tests.rs::header_injection_and_an_address_as_recipient_are_refused`, `no_address_subject_or_relay_reaches_a_log_line` | holds |
| A WASM component reaches a socket, the environment or the file system (AP-147) | the store's `WasiCtx` is built with no environment, no preopened directory, TCP, UDP and name lookup off (`platform crates/wasm-host/src/host.rs:485-489`); outgoing HTTP only to the gateway (`GatewayOnly`) | `host.rs:31,123,485` | ✚ `wasm-host/tests/host_tests.rs::a_component_reaches_no_environment_file_or_socket` (a guest probes all five against a port listening on the host; red when the host inherits the environment and network: `env=81 lookup=resolved tcp=connected`); `outgoing_http_reaches_the_gateway_alone_with_the_callers_token`, `every_request_is_a_fresh_instance_and_two_apps_share_nothing` | holds |
| A function call carries a token an anonymous caller does not have | `functions::call` passes the person's token only | `src/apps/functions.rs` | `apps_functions_tests.rs::an_anonymous_call_to_a_public_app_carries_no_token` | holds |

## 6. Path 4: settings roles

Every change to the configuration is a Change: proposed, checked against the proposer's rights, approved by a person whose role holds `approve` on every kind it touches, and merged by the Portal (PF-49 to PF-52). The seeded roles are the files `components/context-gateway/seed/helsinki/helsinki-role-*.yaml` of joinedcontext-deployment.

| Attack | Where it is decided | Test | Verdict |
|---|---|---|---|
| A project role grants a verb its proposer lacks, or reaches organization scope | `permissions.rs` grant check (PF-52): a Role or RoleBinding is checked against its proposer over its whole scope | `access_escalation_tests.rs::a_project_role_may_not_grant_a_verb_its_proposer_lacks_in_that_project`, `a_role_of_one_project_grants_nothing_in_another_and_nothing_at_organization_scope`, `a_project_role_naming_an_organization_kind_is_refused_at_the_door`, `a_binding_of_a_project_role_outside_its_project_is_refused`, `a_role_is_checked_against_its_proposer_over_the_whole_organization` | holds |
| The same grant smuggled through a workspace, an import or the operations registry | the same check on every way in | `attack_grant_escalation_tests.rs::a_grant_above_the_proposers_rights_is_not_brought_back_from_a_workspace`, `a_grant_above_the_proposers_rights_is_refused_inside_an_import`; `access_escalation_tests.rs::the_operations_registry_refuses_the_same_grant_in_the_same_words` | holds |
| An approver approves a grant above their own rights | the approver is checked like the proposer | `access_escalation_tests.rs::an_approver_approves_a_grant_only_within_their_own_rights_and_a_removal_only_with_delete` | holds |
| A Change approved by its own author | self-approval refused; only an administrator of every kind the Change touches lands their own (a red-lane removal with the name typed back) | `changes_tests.rs::approve_self_approval_returns_403_and_makes_no_git_mutations`, `an_author_who_approves_but_may_not_delete_still_cannot_approve_their_own_change`; `auto_approve_tests.rs::a_stewards_own_change_waits_for_another_approver`, `an_administrator_of_some_of_the_changes_kinds_waits_for_another_approver`, `an_agent_run_never_borrows_its_owners_administrator_standing`, `an_mcp_client_never_approves_on_propose` | holds |
| A person of project A changes project B | the Change's manifests must belong to the path's project | `attack_cross_project_tests.rs::a_bundle_carrying_another_projects_manifest_is_not_approved_from_this_one`, `a_draft_of_another_project_is_not_reachable_through_this_ones_path`; `edge_changes_tests.rs::a_change_is_not_readable_through_another_projects_path` | holds |
| An App owner widens what the App reads or writes in the project | an App's `dataNeeds` compile into an Endpoint and Policies for the App's roles (`apps/reconciler.rs:400-430`, `930-965`); a write or a public visibility makes the Change red lane (jc-core `App::requires_red_lane`, AP-09, AP-10); approving a public App or one with egress needs `publisher` (`permissions.rs:246,302`, AP-120, AP-134) | `changes_tests.rs::approve_red_lane_requires_confirm_and_records_git_actions` | holds, observation O1 below |
| A field the server owns set by the proposer (status, build annotations) | mass assignment refused | `attack_mass_assignment_tests.rs::nobody_proposes_their_own_status`, `nobody_annotates_their_own_build` | holds |
| Every role against every route and operation | one table | `roles_matrix_tests.rs::every_role_meets_every_route_and_operation_exactly_as_the_table_says` | holds |

Observation O1 (no finding, decided by worker-3, owner may override): approving an App approves the data Policies its `dataNeeds` compile into, and nothing compares those needs with the approver's own data rights (the AP-44 comparison, `agents/held.rs`, runs for agent runs only: cg `MATCH (c:Symbol)-[:CALLS]->(f:Symbol) WHERE f.file ENDS WITH 'agents/held.rs' AND NOT c.is_test RETURN DISTINCT c.path` names `api::agent_runs::create_run` alone). The seeded `approver` and `org-admin` roles hold `approve` on `App` and `Policy` together, so it gives nobody more than they can approve directly. A custom Role with `approve` on `App` and not on `Policy` would separate the two; only an organization administrator can write one.

## 7. Path 5: tokens

| Attack | Where it is decided | Test | Verdict |
|---|---|---|---|
| A token of another audience, realm, issuer, key or algorithm at the gateway (audience confusion between the gateway, the broker and App clients) | the gateway verifies per Endpoint audience, issuer, signature with the key table's algorithm (PF-46) | `context-gateway/tests/attack_identity_token_tests.rs` (HMAC with the published key, an algorithm the key is not of, a flood of forgeries), `token_tests.rs`, `edge_auth_token_verify_tests.rs` (audience list, leeway, `nbf`, an issuer one character off) | holds |
| The same at the Portal | `auth/bearer.rs`: `iss`, `aud`, `exp`, `nbf`, signature ES256 or RS256 with a known `kid`; `verify_for_audience` for the second audience, `azp` for the run callbacks | `bearer.rs` unit tests `expired_wrong_audience_wrong_issuer_and_other_key_are_refused`, `a_shared_secret_algorithm_is_refused_even_with_the_right_claims`, `a_tampered_signature_and_an_unsigned_expired_token_are_refused`, `the_audience_is_the_one_the_verifier_was_built_with`; `tests/edge_run_channel_tests.rs::the_run_callbacks_refuse_a_token_of_another_client` | holds |
| The Portal's Keycloak admin rights mint chosen claims: an App's client carries an audience or a mapper its manifest asked for | `reconciler/app_clients.rs`: the client gets the audiences `app-{name}` and the Endpoints `served_endpoints` finds in the App's own project (`:880-890`), and the `groups` mapper; every other protocol mapper is removed and reported (T-2857); no service account, no direct grants (AP-111) | `app_reconciler_tests.rs::a_new_app_whose_client_the_realm_holds_unmanaged_is_refused_at_every_door` | holds |
| A ServiceAccount holds a role of the organization its proposer lacks | PF-52 on the ServiceAccount's bindings | `access_escalation_tests.rs::a_service_account_holds_no_role_of_the_organization_its_proposer_lacks` | holds |
| The Portal reads every Secret of its namespace (T-2479): a Portal flaw that reads a Secret by name reaches the database, Keycloak and artifact-store keys | under `singleNamespace: true` the Apps' namespace is the platform's, and App Secret names are dynamic, so `resourceNames` cannot fence the Portal's Role | none yet: the render test ships with T-2479 step 2 | open: T-2479 (blocked for an integrator-scheduled namespace move, priority 3) |

## 8. Path 6: supply and runtime

The dependencies that take input from a client or an upstream, and the limits the platform has to set on them because their defaults do not.

| Dependency path | Search (production code, test modules excluded) | Verdict |
|---|---|---|
| reqwest without a timeout: `Client::new()` has none | `git grep -n 'reqwest::Client::new()'` in platform and portal, read one by one: outside `#[cfg(test)]` modules and test files three remain. `portal src/space_move.rs:106` is deliberate (an export streams as long as the tenant is large), observation O2; `portal apps/air-quality/src/lib.rs:115` and `apps/hsl-transport/src/lib.rs:140` are sample pod Apps whose backend asks only its own Endpoint, an App's resilience and no platform right (one line in `chyby.md`) | holds |
| reqwest `Client::builder()` without a timeout in its chain | 5 hits: `functions/src/main.rs:85` (every call runs inside the sandbox's `tokio::time::timeout(TIME_LIMIT, …)`, `sandbox.rs:285`), `wasm-host/src/main.rs:64` (inside the request's wall time, `host.rs:571`), `portal src/api/knowledge.rs:129` (per-request `.timeout`, `:173`), `jcctl/src/secrets/bao_http.rs:45,74` (a CLI) | holds |
| axum body limits switched off | `git grep 'DefaultBodyLimit::disable'`: 0 hits; `to_bytes(…, usize::MAX)` outside tests: 0 hits; the gateway reads bodies to the organization's limit (`app.rs:873` `read_body`) | holds |
| tungstenite message and frame sizes (unbounded by default) | gateway client side `fanout/socket.rs:164-166` and server side `handlers/ws.rs:173-174` set both; the Portal upgrades no WebSocket; the broker: threat-model ws W4 (`ws_queue.rs`) | holds |
| hyper client of the gateway to the broker without a timeout of its own | `proxy.rs:240-252`: no connect or response timeout; the edge's route timeout (`components/context-gateway/apisix-routes.yaml` `timeout:` on every route) ends the client request, and the dropped handler future drops the hop; observation O3 | holds |
| rmcp | not a dependency of platform or portal (`Cargo.toml`); the broker's MCP surface is its own (threat-model mcp) | holds |

Observation O2: `space_move.rs` could add a connect timeout and an idle-read timeout without bounding the stream; a broker that accepts and never answers holds one move job, which only an organization administrator starts. Observation O3: the gateway's broker client could carry a connect timeout of its own for in-cluster callers that do not pass the edge. Neither raises a right; both are resilience items for T-3591.

## 9. Antares sweep with a fresh graph (T-3590)

Commit: Antares-NGSI-LD-Context-Broker `b7200437` (dev). Two graphs of it:

```bash
cd Antares-NGSI-LD-Context-Broker
# review graph: the crates that read input, auth, SQL, HTTP indexed as well (36 904 symbols, 79 http routes)
RUSTUP_TOOLCHAIN=1.99.1 cg build --fg --deps axum,hyper,reqwest,sqlx,serde_json,tungstenite,tokio-tungstenite,rmcp,redb,async-nats
# invariant graph: our code only (17 969 symbols, 82 http routes once the T-3590 pins are in)
RUSTUP_TOOLCHAIN=1.99.1 cg build --fg && python3 dev/cg-invariants.py
```

`RUSTUP_TOOLCHAIN` matters: the repository's `rust-toolchain.toml` names `stable`, a toolchain without rust-analyzer in this sandbox, and `cg build` then skips the workspace and indexes only the dependencies, without failing. Neither `jsonwebtoken` nor `wasmtime` is in the broker's `Cargo.lock`.

Vanilla and all-features come from the graph's `features` column, because rust-analyzer indexes every `cfg` branch and tags it. The default build (`default = ["mqtt"]`) serves the 82 routes below. The `views` feature and the surface features (`odata`, `ogc`, `mcp`, `linkml`, `ws`, `internal-csr`) add the `/q` view routes and the `/x` surfaces, which crate-level gating keeps out of the column. Feature-gated non-test functions: `postgres` 112, `views` 82, `fetch` 55, `mqtt` 21, `use-rustls-no-provider` 5, `edm` 4.

### 9.1 Invariants: what the rerun found

`dev/cg-invariants.py` was not green on `dev`. Since T-3962 renamed five crates and T-4354 rewrote the OData parse, nobody had run it against a real graph. The CI job runs only `--selftest`. Fixed on the T-3590 branch:

| Drift | Effect | Fix |
|---|---|---|
| `egress-outside-policy` excepted `antares_jsonld::loader::client_builder`, now `ngsild_jsonld::…` | cg answered with a hint, so the invariant checked nothing | path renamed in `dev/security.cypher` and the allowlist |
| `protocol-crate-depends-on-antares` matched `antares-*` only | `odata-url` could depend on `ngsild-model` (the renamed `antares-model`) unnoticed: the fixture row was missing | also matches `ngsild-*`, and `ogc-cql2` counts as a protocol crate |
| `fs-write-from-a-server` listed `set_permissions` in an `IN` list | a name no code calls is a cg hint, so the job was red | the names are a regex |
| `sql-from-text`: `PgDocStore::list_digests` (T-4354) and `maintenance::retain` | new dynamic SQL, not allowlisted | read: closed table names, constant query parts, every value bound; allowlisted with the reason |
| two stale entries (`antares_odata::batch::serve`, `antares_odata::serve → client_builder`) | T-4354 gave `batch::serve` a direct edge to `Ops::read_answer`; the OData path no longer reaches the CA file read | removed |
| `GET /q/tenants/{}/digest`, `GET …/export`, `POST …/import` had no Channel | the tenant move (ADR-0030) was invisible to every route invariant | pinned in `.cg/links/admin.toml`; allowlisted as admin-secret routes, which `q_admin_sweep.rs` (T-3855) proves |
| a `--deps` graph makes dependency symbols non-external | 17 false `panic-reachable-from-a-route` rows from axum, hyper, reqwest and tungstenite, and lost redb rows | the checker refuses a `--deps` graph |

New invariant `outbound-http-send` lists every function that sends a request with reqwest. Each of the 4 is allowlisted with the egress check its URL passes first. The client's resolver sees hostnames only, so an IP-literal URL is dialled directly unless `Egress::check_url`/`check_destination` or `EgressPolicy::check_host` refused it (SSRF). `dev/cg-invariants/fixture.patch` (refreshed: the old one no longer applied to `Cargo.lock`) turns all 16 invariants red, each with its `cg_fixture` row. With the patch reverted, `cg-invariants: OK`.

### 9.2 Route table

Every http Channel of the invariant graph. "Decision" is what `routes-without-decision` proves: the handler reaches `policy::gate`, `gate_each`, `decide` or `decide_each` within six calls, or the allowlist names the layer that stands in for it. "Tenant" is what `tenant-not-from-header` proves for every route that reaches a Tenant-scoped store method. The bounds wall (`bounds.rs`) caps the URI at 8 KiB, the body at `MAX_BODY_BYTES` and JSON depth at 64 before any parse, on every route. No NATS subject has a Channel: subjects are built at run time and none is reachable from outside the cluster (threat model 9.4).

| Route | Handler | Who may call | Decision | Limits | Writes | Tenant |
|---|---|---|---|---|---|---|
| `POST /ex/v1/remote-notify` | `distsub::remote_notify` | peer broker | its own index, no Policy | bounds wall, body limit | notifications to local subscribers | from the inbound index (ADR-0019) |
| `GET /ngsi-ld/v1/attributes` | `types_attrs::attributes` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/attributes/{}` | `types_attrs::attribute_info` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/csourceRegistrations` | `csource::query_registrations` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/csourceRegistrations` | `csource::create_registration` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/csourceRegistrations/{}` | `csource::delete_registration` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/csourceRegistrations/{}` | `csource::retrieve_registration` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/csourceRegistrations/{}` | `csource::update_registration` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/csourceSubscriptions` | `subscriptions::query_csource_subscriptions` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/csourceSubscriptions` | `subscriptions::create_csource_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/csourceSubscriptions/{}` | `subscriptions::delete_csource_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/csourceSubscriptions/{}` | `subscriptions::retrieve_csource_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/csourceSubscriptions/{}` | `subscriptions::update_csource_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/entities` | `entities::purge_entities` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entities` | `entities::query_entities` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults), geometry ≤ 1024 vertices | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/entities` | `missing_entity_id` | anyone | none: 400 before any store call | bounds wall | none | none |
| `POST /ngsi-ld/v1/entities` | `entities::create_entity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `PUT /ngsi-ld/v1/entities` | `missing_entity_id` | anyone | none: 400 before any store call | bounds wall | none | none |
| `DELETE /ngsi-ld/v1/entities/{}` | `entities::delete_entity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entities/{}` | `entities::retrieve_entity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/entities/{}` | `entities::merge_entity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `PUT /ngsi-ld/v1/entities/{}` | `entities::replace_entity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | replaces | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/entities/{}/attrs` | `attrs::update_attrs` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entities/{}/attrs` | `attrs::append_attrs` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/entities/{}/attrs/{}` | `attrs::delete_attr` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entities/{}/attrs/{}` | `entities::retrieve_entity_attr` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/entities/{}/attrs/{}` | `attrs::partial_update_attr` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `PUT /ngsi-ld/v1/entities/{}/attrs/{}` | `attrs::replace_attr` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | replaces | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entities/{}/attrs/{}/value` | `entities::retrieve_entity_attr_value` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PUT /ngsi-ld/v1/entities/{}/attrs/{}/value` | `attrs::set_attr_value` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | replaces | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entityMaps` | `entity_maps::create_entity_map` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | an EntityMap document (implicit create, 5.14) | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityMaps` | `entity_maps::create_entity_map_post` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/entityMaps/{}` | `entity_maps::delete_entity_map` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/entityMaps/{}` | `entity_maps::retrieve_entity_map` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | an EntityMap document (implicit create, 5.14) | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/entityMaps/{}` | `entity_maps::update_entity_map` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/create` | `batch::batch_create` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, `MAX_BATCH_ITEMS` | creates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/delete` | `batch::batch_delete` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, `MAX_BATCH_ITEMS` | creates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/merge` | `batch::batch_merge` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, `MAX_BATCH_ITEMS` | creates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/query` | `batch::batch_query` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults), geometry ≤ 1024 vertices | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/update` | `batch::batch_update` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, `MAX_BATCH_ITEMS` | creates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/entityOperations/upsert` | `batch::batch_upsert` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, `MAX_BATCH_ITEMS` | creates | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/info/sourceIdentity` | `source_identity` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/jsonldContexts` | `contexts::list_contexts` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/jsonldContexts` | `contexts::add_context` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/jsonldContexts/{}` | `contexts::delete_context` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/jsonldContexts/{}` | `contexts::serve_context` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/snapshots` | `snapshots::purge_snapshots` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/snapshots` | `snapshots::create_snapshot` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/snapshots/{}` | `snapshots::delete_snapshot` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/snapshots/{}` | `snapshots::retrieve_snapshot` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/snapshots/{}` | `snapshots::update_snapshot` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/snapshots/{}/clone` | `snapshots::clone_snapshot` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/subscriptions` | `subscriptions::query_subscriptions` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/subscriptions` | `subscriptions::create_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/subscriptions/{}` | `subscriptions::delete_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/subscriptions/{}` | `subscriptions::retrieve_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/subscriptions/{}` | `subscriptions::update_subscription` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/temporal/entities` | `temporal::query_temporal` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults), geometry ≤ 1024 vertices | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/temporal/entities` | `temporal::upsert_temporal` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/temporal/entities/{}` | `temporal::delete_temporal` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/temporal/entities/{}` | `temporal::retrieve_temporal` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/temporal/entities/{}/attrs` | `temporal::add_temporal_attrs` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/temporal/entities/{}/attrs/{}` | `temporal::delete_temporal_attr` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `DELETE /ngsi-ld/v1/temporal/entities/{}/attrs/{}/{}` | `temporal::delete_temporal_instance` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | deletes | `negotiate::tenant_from` |
| `PATCH /ngsi-ld/v1/temporal/entities/{}/attrs/{}/{}` | `temporal::modify_temporal_instance` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | updates | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/temporal/entityMaps` | `entity_maps::create_temporal_entity_map` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | an EntityMap document (implicit create, 5.14) | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/temporal/entityMaps` | `entity_maps::create_temporal_entity_map_post` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | creates | `negotiate::tenant_from` |
| `POST /ngsi-ld/v1/temporal/entityOperations/query` | `temporal::batch_temporal_query` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults), geometry ≤ 1024 vertices | none | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/types` | `types_attrs::entity_types` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall, page ≤ `max_limit` (else 403 TooManyResults) | none | `negotiate::tenant_from` |
| `GET /ngsi-ld/v1/types/{}` | `types_attrs::entity_type_info` | a caller the Policy admits | `policy::gate`/`decide` (REACH ≤ 6) | bounds wall | none | `negotiate::tenant_from` |
| `GET /q/dead-letters` | `dead_letters_list` | operator (admin secret) | admin secret layer | bounds wall, body limit | none | path or `?tenant=`, `TenantId::new` |
| `DELETE /q/dead-letters/{}` | `dead_letter_delete` | operator (admin secret) | admin secret layer | bounds wall, body limit | deletes | path or `?tenant=`, `TenantId::new` |
| `POST /q/dead-letters/{}/replay` | `dead_letter_replay` | operator (admin secret) | admin secret layer | bounds wall, body limit | re-delivers one dead letter | path or `?tenant=`, `TenantId::new` |
| `GET /q/health` | `health` | anyone (probe) | none, public | bounds wall, body limit | none | none |
| `GET /q/metrics` | `metrics_endpoint` | operator (admin secret unless `metrics_open`) | admin secret layer | bounds wall, body limit | none | none |
| `GET /q/ready` | `ready` | anyone (probe) | none, public | bounds wall, body limit | none | none |
| `GET /q/tenants` | `tenants_list` | operator (admin secret) | admin secret layer | bounds wall, body limit | none | none: the Tenant inventory |
| `DELETE /q/tenants/{}` | `tenant_purge` | operator (admin secret) | admin secret layer | bounds wall, body limit | deletes | path or `?tenant=`, `TenantId::new` |
| `GET /q/tenants/{}` | `tenant_get` | operator (admin secret) | admin secret layer | bounds wall, body limit | none | path or `?tenant=`, `TenantId::new` |
| `GET /q/tenants/{}/digest` | `tenant_move::digest` | operator (admin secret) | admin secret layer | bounds wall, body limit | none | path or `?tenant=`, `TenantId::new` |
| `GET /q/tenants/{}/export` | `tenant_move::export` | operator (admin secret) | admin secret layer | bounds wall, body limit | none | path or `?tenant=`, `TenantId::new` |
| `POST /q/tenants/{}/import` | `tenant_move::import` | operator (admin secret) | admin secret layer | bounds wall, body limit | creates | path or `?tenant=`, `TenantId::new` |

Routes the graph does not key, each with what stands in for the Channel:

| Routes | Built from | Covered by |
|---|---|---|
| `/q/views`, `/q/views/{id}`, `/q/views/{id}/preview`, `/q/schemas/{tenant}`, `/q/policy`, `/q/policy/{tenant}`, `/q/retention/{tenant}`, `…/preview`, `…/deleted`, `/q/quality/{tenant}` (`views`) | closures in `antares_ops::views::admin` and its modules, merged before the admin `route_layer` | `antares-icsr/tests/q_admin_sweep.rs`: the route list is checked against the sources, and every method on every path refuses without the secret |
| `/x/views/{view}/ngsi-ld/v1/{*rest}`, `…/odata/v4…`, `…/ogc/features…`, `…/linkml/{*rest}`, `…/mcp`, `…/ws`, `/x/ws/ngsi-ld/v1` | `ApiSurface::router` of each surface crate, nested by `antares_api::router` | `surface-entry-without-ops` (every entry runs through Ops) and `surfaces_fail_closed.rs`; the per-surface rows of the threat model (ADR-0023) |
| `/grants`, `/grants/{target}/{source}` (`antares_icsr::routes`) | not mounted by the broker binary | none needed while unmounted; mounting it adds a route to `q_admin_sweep.rs` |

### 9.3 Functions ranked by risk

3 953 non-test workspace functions. 1 771 are reachable from a route within four calls (`[:CALLS|DISPATCHES*0..4]` from every route provider). Ranked by branch count × number of routes reaching them. The top tier is every function with `cc ≥ 25` and a score of at least 100, plus every hit of the GREP patterns:

```cypher
MATCH (h:Symbol)-[:PROVIDES]->(:Channel {channel:'http'}) WHERE NOT h.is_test WITH DISTINCT h
MATCH (h)-[:CALLS|DISPATCHES*0..4]->(s:Symbol) WHERE NOT s.is_test AND NOT s.file STARTS WITH '/'
  AND s.kind IN ['Function','Method','StaticMethod']
WITH s, count(DISTINCT h) AS routes WHERE s.cc >= 25
RETURN s.path, s.cc, routes ORDER BY s.cc * routes DESC LIMIT 60;
```

| Function | cc × routes | Risk read for | Verdict |
|---|---|---|---|
| `ngsild_jsonld::expand::expand_instance` | 120 × 12 | recursion on client JSON | safe: request bodies stop at depth 64 at the bounds wall, and federated answers are parsed by serde_json (recursion limit 128) and capped at `MAX_FED_RESPONSE_BYTES` |
| `ngsild_jsonld::expand::expand_entity` | 67 × 19 | as above | safe, as above |
| `antares_api::federation::build_forward` | 55 × 19 | headers copied to a Context Source (credential leak) | safe because documented: `contextSourceInfo` with `urn:ngsi-ld:request` copies a named header (4.3.6.5, an `Authorization` pass-through by design), so creating a registration is a privileged Policy operation (`docs/src/federation.md`). Subject headers are never copied (`a_registration_cannot_ask_for_the_subject_header`), the requesting Tenant never flows through, and the gateway offers no registration operation (platform `attack_dot_segment_tests.rs::the_brokers_admin_registration_and_peer_doors_are_no_endpoint_operation`) |
| `antares_api::temporal::query_temporal_collected` | 120 × 7 | unbounded collection, casts | safe: the page comes from `page_params` (offset ≤ `i64::MAX`, limit ≤ `max_limit`), and the store refuses an unpaged read past its ceiling (`check_ceiling`, 403 TooManyResults) |
| `antares_api::paging::query_doc_params` | 114 × 7 | parameter parsing | safe: every value sits inside the 8 KiB URI, and geometry stops at `MAX_GEO_VERTICES` (1024) |
| `ngsild_jsonld::loader::Loader::fetch` | 33 × 22 | SSRF, size, redirects | safe: `check_host` before the send, at most `MAX_CONTEXT_URLS` per resolution, `MAX_CONTEXT_BYTES` per document, redirects re-checked and capped at 3 (`client_builder`) |
| `antares_api::entities::filter_entities_paged` | 78 × 8 | casts on client numbers | finding F3, fixed: the conditional-rules scan cast `offset + limit + 1` and its doubling limit with `as i64`, so an offset near `i64::MAX` wrapped into a negative LIMIT, which Postgres refuses (500). Now `entities::store_page` saturates; `the_scan_page_past_i64_saturates_and_never_wraps_negative` |
| `ngsild_ql::geo::GeoQuery::from_params` | 35 × 17 | geometry size | safe: `MAX_GEO_VERTICES` |
| `antares_api::notify::deliver_as` | 72 × 8 | SSRF via a subscription endpoint | safe: `Egress::check_destination` before `send_outbound`; the sink keeps the checked URI for its retries |
| `antares_api::batch::batch_write` | 126 × 4 | batch size | safe: `parse_batch` refuses more than `MAX_BATCH_ITEMS` |
| `antares_api::paging::order_entities` | 81 × 5 | sort cost | safe: sorts the rows already read, which the store ceiling bounds (5.5.6) |
| `antares_api::subscriptions::norm_member` | 65 × 4 | deserialisation | safe: each named member is type-checked (an id through `EntityId::new`); an unknown member is kept verbatim as 5.5.9 asks, inside the body cap and the depth limit |
| `antares_api::repr::parse_projection` | 31 × 8 | `pick`/`omit` size | safe: bounded by the 8 KiB URI |
| `antares_api::batch::forward_batch` | 59 × 4 | SSRF | safe: `Egress::check_url` before every forward |
| GREP: `Regex::new`/`RegexBuilder::new` (4 non-test) | | regex from input | safe: `ngsild_ql::regex::build` and `ngsild_residual::pattern::build` set `size_limit` and `dfa_size_limit`; `odata_csdl` compiles a constant; the one at `ngsild_ql/src/regex.rs:300` is inside a `#[test]` that cg marks non-test |
| GREP: TLS switched off, `unsafe` | | | 0 hits in `crates/*/src` |
| GREP: secret compare | | | `AdminAuth::admits` uses `constant_time_eq`; the one other `==` on a token is HTTP method parsing (`odata_batch::multipart::request_line`) |

Zero open priority-1 findings in the broker. F3 is a 500 on an absurd offset, with no data and no right gained, and it is fixed in the same branch.

## 10. joinedcontext-platform sweep with a fresh graph (T-3591)

Commit: joinedcontext-platform `a69980d` (main). Two graphs of it:

```bash
cd joinedcontext-platform
# invariant graph (17 336 symbols, 85 http Channels with the T-3591 link rules)
RUSTUP_TOOLCHAIN=1.99.1 cg build --fg && python3 dev/cg-invariants.py
# review graph (25 219 symbols)
RUSTUP_TOOLCHAIN=1.99.1 cg build --fg --deps axum,hyper,reqwest,sqlx,serde_json,jsonwebtoken,tokio-tungstenite
```

`wasmtime` is left out of `--deps`: its size would outweigh the rest, and T-3588 path 3 already proves the sandbox with an attack test (`host_tests.rs::a_component_reaches_no_environment_file_or_socket`). Neither `rmcp` nor `async-nats` is a platform dependency.

### 10.1 Link rules and invariants

Before T-3591, `.cg/links` keyed only the gateway's `app.rs`, so `ROUTES` missed six servers. `.cg/links/services.toml` adds agent-proxy, the assistant (chat, widget and the Portal's knowledge admin), the gateway's MQTT auth hook, functions, token-sidecar and the wasm-host shard's two ports. The gateway's own hop pins in `gateway.toml` still name `serve_ngsi_ld` and `egress::notifications`, which T-3643 and T-3654 deleted (logged in chyby.md, with the stale `dev/trace-request.py`).

`dev/cg-invariants.py` was red on `main`, for the same reason as the broker's: CI runs only `--selftest`.

| Drift | Fix |
|---|---|
| `Exchanger::exchange`/`Exchanger::new` (federation token exchange, PF-47) not allowlisted | read; the client now follows no redirect (F4 below), then allowlisted |
| `camera_sim::token` (token request and a bare `Client::new`) | a demo simulator, not a deployed service: allowlisted with that reason |
| `jcctl::commands::publish_ckan::example_type` | the operator's CLI asking the URL the operator publishes: allowlisted |
| two `secret-exposed` entries for the gateway's delivery key | stale: T-3654 deleted the key; removed |
| a `--deps` graph | refused by the checker, as in the broker |

New invariant `route-without-authentication`: every http route whose handler reaches none of the caller checks (`gateway::auth::token::Verifier::verify`, `agent_proxy::auth::authenticate`, `agent_proxy::routes::identity::portal_only`, `assistant::chat::admin::the_portal`) within six calls. Its 21 rows are allowlisted with the reason they need none: protected-resource metadata, the public DCAT feed, the widget assets of public deployments, the probes, in-cluster metrics, the network-guarded token sidecar, and the wasm apps port (an App never sees the caller's `Authorization` or `Cookie`; the host carries the token to the gateway). The fixture (`dev/cg-invariants/fixture.patch`) turns all 7 invariants red. Without it: `cg-invariants: OK`.

### 10.2 Route table

Every http Channel of the invariant graph (80 distinct route and handler pairs; the gateway mounts five of them twice, with and without a trailing slash). Limits: the gateway reads a body up to the organization's limit (`app.rs` `read_body`), agent-proxy through `routes::body::bounded`, functions under `DefaultBodyLimit::max(INVOCATION_LIMIT)`, the assistant's chat under `DefaultBodyLimit::max(MAX_BODY)`, and its admin upload under its own `DefaultBodyLimit`. WebSocket frame and message sizes are set (section 8). Tenant scoping in the gateway is the Endpoint's Context Space, resolved from the slug (`resolver`). Each other service scopes by its own principal: the run (agent-proxy), the project path segment checked against the Portal's token (assistant admin), the deployment's public id (assistant chat).

| Route | Handler | Who may call | Caller check (REACH ≤ 6) | Writes |
|---|---|---|---|---|
| `ANY /v1/data/endpoints/{}/{}` | `agent_proxy::routes::data::endpoint_handler` | a run's workspace | run token `auth::authenticate`, `DataCredentials::verify` | what the forwarded operation writes, through the broker |
| `ANY /v1/data/{}` | `agent_proxy::routes::data::handler` | a run's workspace | run token `auth::authenticate`, `DataCredentials::verify` | what the forwarded operation writes, through the broker |
| `GET /v1/diagnostics/{}/{}` | `agent_proxy::routes::diagnostics::handler` | a run's workspace | run token `auth::authenticate` | none |
| `POST /v1/runs/events` | `agent_proxy::routes::events::handler` | a run's workspace | run token `auth::authenticate` | creates or forwards |
| `GET /v1/fetch` | `agent_proxy::routes::fetch::handler` | a run's workspace | run token `auth::authenticate` | none |
| `ANY /v1/forge/{}` | `agent_proxy::routes::forge::handler` | a run's workspace | run token `auth::authenticate` | what the forwarded operation writes, through the broker |
| `POST /internal/runs/{}/data-credential` | `agent_proxy::routes::identity::data_credential` | the Portal | `portal_only` | creates or forwards |
| `POST /internal/runs/{}/identity` | `agent_proxy::routes::identity::handler` | the Portal | `portal_only` | creates or forwards |
| `GET /v1/runs/inbox` | `agent_proxy::routes::inbox::handler` | a run's workspace | run token `auth::authenticate` | none |
| `POST /v1/llm/{}` | `agent_proxy::routes::llm::handler` | a run's workspace | run token `auth::authenticate` | creates or forwards |
| `POST /v1/mcp` | `agent_proxy::routes::mcp::handler` | a run's workspace | run token `auth::authenticate` | creates or forwards |
| `GET /v1/packages/{}/{}` | `agent_proxy::routes::packages::handler` | a run's workspace | run token `auth::authenticate` | none |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/documents` | `assistant::chat::admin::documents` | the Portal | `the_portal` | none |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/errors` | `assistant::chat::admin::errors` | the Portal | `the_portal` | none |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/files` | `assistant::chat::admin::files` | the Portal | `the_portal` | none |
| `POST /internal/v1/projects/{}/knowledge/sources/{}/inclusion` | `assistant::chat::admin::inclusion` | the Portal | `the_portal` | creates or forwards |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/pages/{}/links` | `assistant::chat::admin::links` | the Portal | `the_portal` | none |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/pages` | `assistant::chat::admin::pages` | the Portal | `the_portal` | none |
| `GET /internal/v1/projects/{}/knowledge/sources/{}/passages` | `assistant::chat::admin::passages` | the Portal | `the_portal` | none |
| `POST /internal/v1/projects/{}/knowledge/sources/{}/recrawl` | `assistant::chat::admin::recrawl` | the Portal | `the_portal` | creates or forwards |
| `DELETE /internal/v1/projects/{}/knowledge/sources/{}/files/{}` | `assistant::chat::admin::remove` | the Portal | `the_portal` | deletes |
| `GET /internal/v1/projects/{}/knowledge/sources` | `assistant::chat::admin::sources` | the Portal | `the_portal` | none |
| `POST /internal/v1/projects/{}/knowledge/sources/{}/files` | `assistant::chat::admin::upload` | the Portal | `the_portal` | creates or forwards |
| `GET /internal/v1/projects/{}/knowledge/deployments/{}/usage` | `assistant::chat::admin::usage` | the Portal | `the_portal` | none |
| `POST /api/v1/d/{}/chat` | `assistant::chat::chat` | anyone, for a public deployment | none: a public deployment's anonymous chat by its public id | creates or forwards |
| `POST /internal/v1/projects/{}/knowledge/deployments/{}/chat` | `assistant::chat::chat_in_portal` | the Portal | `the_portal` | creates or forwards |
| `POST /api/v1/d/{}/feedback` | `assistant::chat::feedback` | anyone, for a public deployment | none: anonymous feedback on a public deployment's answer | creates or forwards |
| `OPTIONS /api/v1/d/{}/feedback` | `assistant::chat::preflight` | anyone, for a public deployment | none: CORS preflight of a public deployment's feedback | none |
| `OPTIONS /api/v1/d/{}/chat` | `assistant::chat::preflight` | anyone, for a public deployment | none: CORS preflight of a public deployment's chat | none |
| `GET /d/{}` | `assistant::chat::widget::full_page` | anyone, for a public deployment | none: a public assistant deployment's page by its public id | none |
| `GET /d/{}/embed.js` | `assistant::chat::widget::launcher` | anyone, for a public deployment | none: the launcher script of a public deployment by its public id | none |
| `GET /d/{}/widget` | `assistant::chat::widget::page` | anyone, for a public deployment | none: the widget page of a public deployment by its public id | none |
| `GET /d/widget.js` | `assistant::chat::widget::script` | anyone, for a public deployment | none: the embeddable chat widget's static script | none |
| `GET /d/widget.css` | `assistant::chat::widget::style` | anyone, for a public deployment | none: the embeddable chat widget's static stylesheet | none |
| `GET /catalog.jsonld` | `gateway::app::catalog_feed_jsonld` | an Endpoint's audience (public, organization or a granted ServiceAccount) | none: the public DCAT catalogue: only public Endpoints are listed | none |
| `GET /catalog.ttl` | `gateway::app::catalog_feed_turtle` | an Endpoint's audience (public, organization or a granted ServiceAccount) | none: the public DCAT catalogue as Turtle | none |
| `GET /api/endpoint/{}` | `gateway::app::endpoint_record` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `POST /api/endpoint/{}/mcp` | `gateway::app::mcp_message` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | creates or forwards |
| `ANY /api/endpoint/{}/ngsi-ld/v1/{}` | `gateway::app::ngsi_ld` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | what the forwarded operation writes, through the broker |
| `GET /api/endpoint/{}/ngsi-ld/v1` | `gateway::app::ngsi_ld_entry` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `GET /healthz` | `gateway::app::ok` | in-cluster probe or scraper | none: liveness probe | none |
| `GET /livez` | `gateway::app::ok` | in-cluster probe or scraper | none: liveness probe | none |
| `GET /api/endpoint/{}/.well-known/oauth-protected-resource` | `gateway::app::protected_resource` | an Endpoint's audience (public, organization or a granted ServiceAccount) | none: RFC 9728 metadata a client reads before it has a token: the issuer and the audience | none |
| `POST /cs/{}/mcp` | `gateway::app::space_mcp_message` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | creates or forwards |
| `ANY /cs/{}/ngsi-ld/v1/{}` | `gateway::app::space_ngsi_ld` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | what the forwarded operation writes, through the broker |
| `GET /api/endpoint/{}/file.csv` | `gateway::handlers::files::file_csv` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `ANY /api/endpoint/{}/odata/v4/{}` | `gateway::handlers::ogc::odata` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `ANY /api/endpoint/{}/odata/v4` | `gateway::handlers::ogc::odata` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `ANY /api/endpoint/{}/ogc/features` | `gateway::handlers::ogc::ogc_features` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `ANY /api/endpoint/{}/ogc/features/{}` | `gateway::handlers::ogc::ogc_features` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `GET /api/endpoint/{}/openapi.json` | `gateway::handlers::openapi_doc::openapi` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `GET /api/endpoint/{}/schema/{}/{}` | `gateway::handlers::schema_routes::schema_artifact` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `GET /api/endpoint/{}/schema/index.json` | `gateway::handlers::schema_routes::schema_index` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `GET /cs/{}/schema/{}/{}` | `gateway::handlers::schema_routes::space_schema_artifact` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `GET /cs/{}/schema/index.json` | `gateway::handlers::schema_routes::space_schema_index` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `GET /cs` | `gateway::handlers::space_routes::space_catalog` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `GET /cs/{}` | `gateway::handlers::space_routes::space_record` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `ANY /api/endpoint/{}/sta/v1.1` | `gateway::handlers::sta::sensorthings` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `ANY /api/endpoint/{}/sta/v1.1/{}` | `gateway::handlers::sta::sensorthings` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | what the forwarded operation writes, through the broker |
| `GET /api/endpoint/{}/access` | `gateway::handlers::view_access::access` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none |
| `POST /api/endpoint/{}/access/check` | `gateway::handlers::view_access::access_check` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none (a decision preview) |
| `POST /api/endpoint/{}/access/simulate` | `gateway::handlers::view_access::access_simulate` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Endpoint::admits` | none (a decision preview) |
| `GET /api/endpoint/{}/ws` | `gateway::handlers::ws::endpoint_ws` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `GET /api/endpoints` | `gateway::mcp::hub::endpoints` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | none |
| `GET /api/mcp/.well-known/oauth-protected-resource` | `gateway::mcp::hub::protected_resource` | an Endpoint's audience (public, organization or a granted ServiceAccount) | none: RFC 9728 metadata of the MCP hub | none |
| `POST /api/mcp/{}/{}` | `gateway::mcp::server::message` | an Endpoint's audience (public, organization or a granted ServiceAccount) | token `Verifier::verify`, `Pdp::decide` | creates or forwards |
| `GET /api/mcp/{}/{}/.well-known/oauth-protected-resource` | `gateway::mcp::server::protected_resource` | an Endpoint's audience (public, organization or a granted ServiceAccount) | none: RFC 9728 metadata of one MCP server | none |
| `POST /internal/mqtt/auth` | `gateway::mqtt::auth` | the MQTT broker's auth hook | token `Verifier::verify`, `mqtt::decide` | creates or forwards |
| `GET /metrics` | `gateway::telemetry::metrics` | in-cluster probe or scraper | none: Prometheus scrape inside the cluster | none |
| `POST /invoke` | `functions::invoke` | a caller with a gateway-verified token | token `Verifier::verify` | creates or forwards |
| `POST /token` | `token_sidecar::token` | the pipeline runner pod | none: the pipeline runner's token service: the deployment's NetworkPolicy admits the runner pod alone to 4180 (pipeline-runner networkpolicies.yaml pipeline-tokens) | creates or forwards |
| `ANY /{}/{}` | `wasm_host::server::Shard::handle` | the edge (apps port) or in-cluster (admin port) | none: the apps port: the App never sees the caller's Authorization or Cookie (host.rs Host::serve_admitted removes both) | what the forwarded operation writes, through the broker |
| `GET /jobs` | `wasm_host::server::Shard::handle_admin` | the edge (apps port) or in-cluster (admin port) | none: the shard's admin port | none |
| `GET /metrics` | `wasm_host::server::Shard::handle_admin` | the edge (apps port) or in-cluster (admin port) | none: the shard's admin port | none |

### 10.3 Functions ranked by risk

3 028 non-test workspace functions. 1 853 are reachable from a route within four calls. The ranking query is the one in 9.3 with `cc ≥ 20`. The top tier and the GREP patterns:

| Function | cc × routes | Risk read for | Verdict |
|---|---|---|---|
| `gateway::handlers::reads::paged_view` | 30 × 7 | paging numbers | safe: `limit` parses as `u64` and is capped at the Endpoint's `max_rows`; the client's `offset` is dropped and the gateway pages the broker's view itself |
| `gateway::app::serve_ngsi_ld_view` | 44 × 2 | the decision and the hop | safe: T-3588 path 1; dot segments refused before the hop since F1 |
| `gateway::operations::operation_of` | 42 × 2 | parser differential | fixed in F1 (T-4389) |
| `agent_proxy::routes::data::forward` | 42 × 2 | a run reaching another Endpoint | safe: `/v1/data/endpoints/{slug}` admits only a slug the run names, checked once more against the Portal's record when the cache does not name it (`data.rs:116-122`), after `auth::authenticate` |
| `agent_proxy::routes::forge::handler` | 38 × 1 | path traversal with the platform's forge token | safe: `escapes` refuses `%`, `\`, `//`, leading `/` and dot segments; a write must start with the run's `pathPrefix` (the Portal sends it with a trailing `/`), and nothing under `.gitea/`; only `contents/`, branches, pull requests and read-only `pulls/`/`commits/` pass |
| `gateway::app::view_payload`, `gateway::app::HeldWrite::check` | 41 × 2, 29 × 2 | write narrowing | safe: T-3588 path 2 (hidden attributes, held writes) |
| `gateway::translators::sta::filter_to_q` | 21 × 1 | injection into `q` | safe: an operand passes through unescaped, so a caller can write any `q`; a `q` only narrows inside the view the broker enforces, and grants nothing |
| `gateway::translators::tabular::parquet`, `handlers::files::tabular_download` | 20 × 3 | output size | safe: refused past the Endpoint's `maxFileBytes` (T-4287) |
| `gateway::fanout::socket::session`, `::run` | 35 × 1, 28 × 1 | WebSocket floods | safe: frame and message caps (section 8), each push decided again (`fanout::hub::decide`) |
| `gateway::mcp::server::Hub::fan_out`, `mcp::hub::Hub::call` | 28 × 1, 25 × 1 | one call fanning out to many Endpoints | safe: both reach `Verifier::verify` and `Pdp::decide` (route table), so each Endpoint the call reaches is decided on its own |
| `gateway::federation::exchange::Exchanger::exchange` | via `federation_token` | credential carry-over | finding F4, fixed |
| `functions::sandbox::execute`, `functions::invoke` | 22 × 1, 20 × 1 | sandbox escape, size | safe: `Verifier::verify` first, `INVOCATION_LIMIT`, the sandbox's `tokio::time::timeout` (section 8) |
| `wasm_host::host::Host::serve_admitted` | 22 × 1 | credential to the guest | safe: `Authorization` and `Cookie` removed before the guest sees the request; body over `request_bytes` refused |
| `assistant::upload::office` | 21 × 1 | untrusted Office files | safe: behind `the_portal`, under the admin body limit; kreuzberg reads members in memory and calls no `extract` (T-4383) |
| `gateway::auth::token::Verifier::verify` | caller check | JWT validation | safe: the algorithm comes from the JWKS entry for the `kid`, never from the token header; issuer, audience, `exp` and `nbf` are checked, with 60 s leeway; an empty audience list refuses |
| GREP: `Regex::new` (48 non-test) | | regex from input | constants, except `pdp::projection::id_permitted` and `jc_core::kinds::role::whole_value`, which compile patterns from a Policy or Role that the project's administrators write, validated at commit (`EntitySelector::validate`, `PATTERN_MAX` 256); the regex crate runs in linear time. Observation: `id_permitted` compiles each pattern per entity (chyby.md) |
| GREP: TLS switched off, `unsafe` | | | 0 hits in `crates/*/src` |
| GREP: secrets in log calls | 24 hits | | each logs that a token is missing or was refused, never its value |

**F4 (fixed in T-3591).** Three clients that post a credential to a token endpoint kept reqwest's default redirect policy: the gateway's federation exchange (the caller's token and the gateway's client secret), the gateway's workload token (its ServiceAccount assertion) and token-sidecar (the pipeline's assertion). A 307 or 308 from the configured endpoint made reqwest resend the form, credential included, to whatever host the answer named. Reaching that needs a compromised or misconfigured realm. No caller can trigger it, so it is not priority 1. All three now set `redirect::Policy::none()`. Tests: `exchange::tests::a_redirect_from_the_token_endpoint_is_refused_and_the_form_goes_nowhere_else` (red with the default policy) and `previews_federated_tests::a_redirecting_token_endpoint_is_not_followed`.

Zero open priority-1 findings in the platform.

## 11. joinedcontext-portal sweep with a fresh graph (T-3592)

Commit: joinedcontext-portal `e07e0c89` (main). One graph, built with the toolchain that ships rust-analyzer:

```bash
cd joinedcontext-portal
RUSTUP_TOOLCHAIN=1.99.1 cg build --fg && python3 dev/cg-invariants.py
```

The graph holds 42 674 symbols (Rust, plus the embedded React UI under `ui/`), 231 http Channels and 1 377 links from the UI's calls to the routes.

### 11.1 Link rules and invariants

`dev/cg-invariants.py` was red on `main` here too, because CI runs only `--selftest`.

| Drift | Fix |
|---|---|
| `public_docs::serve` (the public API and User Guide pages, T-4063) has no identity | read: a chapter from a fixed list, a page name of `[A-Za-z0-9_-]`, raw HTML filtered by `safe`; allowlisted |
| `outbound-http` in `mqtt_measured.rs`, `quality_checks.rs`, `space_move.rs` | read: every host comes from configuration or a declared resource's DNS-1123 name, never from a request value; `space_move` is for organization administrators only (`require_organization_admin`); allowlisted with those reasons |
| `route-without-caller` for `GET`/`POST …/spaces/{}/move` and `POST /internal/pipelines/{}/{}/samples` | documented API without a UI page yet (PF-111), and the pipeline runner's taps on the internal listener (T-3974); allowlisted |
| `POST …/knowledge/sources/{s}/files` showed no caller | the UI's `uploadFile` builds the URL from a `FormData` call cg does not follow; pinned in `.cg/links/ui.toml` |
| the realm admin clients followed redirects | finding F5 below |

The fixture (`dev/cg-invariants/fixture.patch`) still turns every invariant red. Without it: `cg-invariants: OK`.

Decided (worker-3, owner may override): no new invariant for the redirect policy of the Portal's 34 `reqwest` client constructions, because the clients that carry a credential to a host outside the cluster (`auth::oidc`, `agents::identity`, and since F5 the three realm admin clients) all set `Policy::none()`, and the rest call in-cluster services from configuration, where reqwest already drops `Authorization` on a cross-host redirect. Options: an invariant on every `Client::builder` without `.redirect(`, with an allowlist of about 30 rows.

### 11.2 Route table

Every http Channel of the graph: 232 route and handler pairs. 161 reach `permissions::for_request` (the signed-in user's rights in the project) within six calls. 12 use the internal listener's bearer (`auth::internal`). 12 use an App token (`BearerVerifier`), 2 of them through the App session. 47 need none, each with its reason in the table.

| Route | Handler | Decision (REACH ≤ 6) | Writes |
|---|---|---|---|
| `DELETE /api/v1/alerts/{}` | `api::alerts::unsubscribe` | none needed: DELETE ... WHERE id = $1 AND subject = $2: another caller's id is 404 | deletes |
| `DELETE /api/v1/organization/people/{}` | `api::people::delete_person` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/organization/people/{}/consents/{}` | `api::people::revoke_consent` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}` | `api::projects::delete_project` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/drafts/{}/{}` | `api::drafts::drop_draft` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/knowledge/sources/{}/files/{}` | `api::knowledge::remove_file` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/pipelinetemplates/{}` | `api::pipeline_templates::delete_pipeline_template` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/serviceaccounts/{}/keys/{}` | `api::service_accounts::revoke_key` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/spaces/{}/comments/{}` | `api::entity_comments::remove_comment` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/spaces/{}/trash/{}` | `api::entity_trash::forget` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/spaces/{}/views/{}` | `api::data_views::delete_view` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/workspaces/{}` | `api::workspaces::discard_workspace` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/workspaces/{}/preview` | `api::workspaces::stop_workspace_preview` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /api/v1/projects/{}/{}/{}` | `api::delete::delete_resource` | `permissions` (signed-in user's rights in the project) | deletes |
| `DELETE /apps/{}/api/services/files/{}` | `apps::files::delete` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | deletes |
| `GET /.well-known/oauth-protected-resource` | `mcp::oauth_protected_resource` | none needed: RFC 9728 metadata, public by the standard | none |
| `GET /.well-known/oauth-protected-resource/api/v1/mcp` | `mcp::oauth_protected_resource` | none needed: RFC 9728 metadata, public by the standard | none |
| `GET /api/v1/alerts` | `api::alerts::list_alerts` | none needed: the caller's own subscriptions, selected by subject | none |
| `GET /api/v1/alerts/notices` | `api::alerts::list_notices` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/app-templates` | `api::app_templates::list_templates` | none needed: the built-in template list compiled into the binary | none |
| `GET /api/v1/app-templates/{}/screenshot/{}` | `api::app_templates::screenshot` | none needed: embedded PNG; the name must be a known template and the width 1440 or 375, so no path is built from input | none |
| `GET /api/v1/auth/callback` | `auth::oidc::callback` | none needed: login: state and PKCE verifier from the private cookie jar | none |
| `GET /api/v1/auth/login` | `auth::oidc::login` | none needed: login start | none |
| `GET /api/v1/auth/me` | `auth::oidc::me` | none needed: the caller's own identity | none |
| `GET /api/v1/auth/sso-check` | `auth::oidc::sso_check` | none needed: silent SSO probe, a redirect to the realm | none |
| `GET /api/v1/auth/sso-check/done` | `auth::oidc::sso_check_done` | none needed: static page ending the probe | none |
| `GET /api/v1/blueprints` | `api::blueprints::list_blueprints` | none needed: filters every Blueprint through may_run on the caller's identity | none |
| `GET /api/v1/branding` | `api::branding::get_branding` | none needed: the installation's public branding | none |
| `GET /api/v1/branding/{}` | `api::branding::get_asset` | none needed: the installation's public branding | none |
| `GET /api/v1/catalogue` | `api::catalogue::get_catalogue` | none needed: the open-data catalogue: only audience public Endpoints, private datasets dropped (EP-81) | none |
| `GET /api/v1/catalogue/datasets/{}` | `api::catalogue::get_dataset` | none needed: one public dataset (EP-82) | none |
| `GET /api/v1/catalogue/datasets/{}/sample` | `api::catalogue::get_sample` | none needed: reads a public Endpoint with no credential, so the gateway's own anonymous policy decides what comes back | none |
| `GET /api/v1/endpoints` | `api::resources::list_endpoints_everywhere` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/forms` | `api::forms::list_forms` | none needed: the organization's form arrangements (UISchema), the same for every person; no project data | none |
| `GET /api/v1/health` | `api::health::health` | none needed: liveness probe | none |
| `GET /api/v1/mcp` | `mcp::router` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/notifications` | `api::entity_comments::list_notifications` | none needed: the caller's own notifications, selected by recipient | none |
| `GET /api/v1/openapi.json` | `openapi::openapi_json` | none needed: the published API contract | none |
| `GET /api/v1/organization/brokers` | `api::brokers::list_brokers` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/datamodels` | `api::datamodels::list_organization_datamodels` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/feedback` | `api::feedback::list_feedback` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/feedback/{}/screenshot` | `api::feedback::feedback_screenshot` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/health` | `api::validation::get_health` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/limits` | `api::organization_limits::get_limits` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/people` | `api::people::list_people` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/people/{}` | `api::people::get_person` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/organization/setup` | `api::setup::get_setup` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/preferences` | `api::preferences::get_preferences` | none needed: the caller's own preferences, keyed by subject | none |
| `GET /api/v1/projects` | `api::projects::list_projects` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}` | `api::projects::get_project` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/activity` | `api::activity::list_activity` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/activity/stream` | `api::activity::stream_activity` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/agent-runs` | `api::agent_runs::list_runs` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/agent-runs/{}` | `api::agent_runs::get_run` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/agent-runs/{}/events` | `api::agent_runs::stream_events` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/agent-runs/{}/preview` | `api::agent_runs::preview` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/app-checks` | `api::validation::list_app_checks` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/apps/{}/build` | `api::app_build::build` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/apps/{}/builds` | `api::app_build::builds` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/apps/{}/export` | `api::app_transfer::export_app` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/apps/{}/me` | `api::app_me::me` | App token (`BearerVerifier::verify_app_token`, `session_of`) | none |
| `GET /api/v1/projects/{}/assistant/access` | `api::assistant::get_access` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/assistant/catalog` | `api::assistant::get_catalog` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/basemap/{}/style.json` | `api::basemap::get_style` | none needed: the public basemap style; project checked against the mirror | none |
| `GET /api/v1/projects/{}/basemap/{}/{}/{}/{}` | `api::basemap::get_tile` | none needed: public tiles: style fixed to default, z/x/y parsed as integers in range, extension allowlisted, so the cache path takes no free text | none |
| `GET /api/v1/projects/{}/changes` | `api::changes::list_changes` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/changes/history` | `api::changes::list_change_history` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/changes/{}` | `api::changes::get_change` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/changes/{}/manifests` | `api::restore::change_manifests` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/ckan/status` | `api::ckan::get_status` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/datamodels/{}/source` | `api::datamodels::get_source` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/drafts` | `api::drafts::list_drafts` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/drafts/events` | `api::drafts::stream_draft_events` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/drafts/{}/{}` | `api::drafts::get_draft` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/drift` | `api::drift::list_drift` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/export` | `api::export::export` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/federation-graph` | `api::federation::get_graph` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/deployments/{}/usage` | `api::knowledge::deployment_usage` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources` | `api::knowledge::list_sources` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/documents` | `api::knowledge::list_documents` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/errors` | `api::knowledge::list_errors` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/files` | `api::knowledge::list_files` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/pages` | `api::knowledge::list_pages` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/pages/{}/links` | `api::knowledge::list_links` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/knowledge/sources/{}/passages` | `api::knowledge::list_passages` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/mqttbrokers/{}/measured` | `api::mqtt_measured::get_measured` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/ops` | `api::ops::list_ops` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/permissions/me` | `api::permissions::permissions_me` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/metrics` | `api::pipelines::get_metrics` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/rejected` | `api::pipelines::get_rejected` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/runs` | `api::pipelines::get_runs` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/runs/{}/log` | `api::pipelines::get_run_log` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/samples` | `api::pipelines::get_samples` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelines/{}/stats` | `api::pipelines::get_stats` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/pipelinetemplates` | `api::pipeline_templates::list_pipeline_templates` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/registry` | `api::projects::get_registry_entry` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/revisions` | `api::export::revisions` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/serviceaccounts/{}/keys` | `api::service_accounts::list_keys` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/serviceaccounts/{}/keys/claims/{}` | `api::service_accounts::get_key_claim` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/comments` | `api::entity_comments::list_comments` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/live` | `api::live::watch` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/move` | `api::space_move::get_move` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/quality` | `api::quality::get_quality` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/quality/checks` | `api::quality::get_checks` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/retention/deleted` | `api::space_retention::retention_deleted` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/trash` | `api::entity_trash::list_trash` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/types/{}/attributes` | `api::type_attributes::type_attributes` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/usage` | `api::space_usage::get_usage` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/views` | `api::data_views::list_views` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/spaces/{}/views/{}` | `api::data_views::get_view` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/syncsources/{}/status` | `api::sync_sources::status` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/workspaces` | `api::workspaces::list_workspaces` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/workspaces/{}` | `api::workspaces::get_workspace` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/workspaces/{}/compare` | `api::workspaces::compare_workspace` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/workspaces/{}/preview` | `api::workspaces::get_workspace_preview` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/{}` | `api::resources::list` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/projects/{}/{}/{}` | `api::resources::get_resource` | `permissions` (signed-in user's rights in the project) | none |
| `GET /api/v1/ready` | `api::health::ready` | none needed: readiness probe | none |
| `GET /api/v1/sync` | `api::sync::get_sync_status` | none needed: the instance's mirror state (revision, count, error text without credentials); no project data | none |
| `GET /api/v1/tools/sdm-catalog` | `tools::model_tools::sdm_catalog` | none needed: the public Smart Data Models index; the URL is Model Tools' own allowlist, the caller steers only refresh | none |
| `GET /apps/{}` | `apps::static_host::serve_index` | the App's visibility, through the App session (`BearerVerifier::verify_app_token`, `session_of`) | none |
| `GET /apps/{}/api/services/files` | `apps::files::list` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | none |
| `GET /apps/{}/api/services/files/{}` | `apps::files::get` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | none |
| `GET /apps/{}/api/services/jobs` | `apps::jobs::list` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | none |
| `GET /apps/{}/{}` | `apps::static_host::serve` | the App's visibility, through the App session (`BearerVerifier::verify_app_token`, `session_of`) | none |
| `GET /internal/agent-runs/{}` | `api::internal::agent_runs::internal_get_run` | internal bearer (`auth::internal`) | none |
| `GET /internal/agent-runs/{}/diagnostics/{}/{}` | `api::internal::agent_runs::internal_diagnostics` | internal bearer (`auth::internal`) and `permissions` | none |
| `GET /internal/agent-runs/{}/inbox` | `api::internal::agent_runs::internal_inbox` | internal bearer (`auth::internal`) | none |
| `GET /internal/domain-verifications` | `api::internal::domain_verifications::domain_verifications` | internal bearer (`auth::internal`) | none |
| `GET /internal/previews` | `api::internal::previews::served_previews` | internal bearer (`auth::internal`) | none |
| `GET /mail/unsubscribe` | `apps::email::unsubscribe_page` | none needed: same signed link; the page echoes only a DNS-1123 app name under a default-src 'none' CSP | none |
| `GET /metrics` | `telemetry::metrics` | none needed: /metrics: the edge refuses the path (deployment components/portal/apisix-routes.yaml portal-metrics), scraped in-cluster only | none |
| `GET /templates/assets/{}` | `apps::template_demos::asset` | none needed: the built-in templates' demo files, CSP forbids every connection (AP-141) | none |
| `GET /templates/{}` | `apps::template_demos::demo` | none needed: the built-in templates' demo pages (AP-141) | none |
| `GET /templates/{}` | `apps::template_demos::demo` | none needed: the built-in templates' demo pages (AP-141) | none |
| `PATCH /api/v1/organization/people/{}` | `api::people::edit_person` | `permissions` (signed-in user's rights in the project) | updates |
| `PATCH /api/v1/projects/{}/{}/{}` | `api::mutate::patch` | `permissions` (signed-in user's rights in the project) | updates |
| `POST /api/v1/activity` | `api::activity::ingest_activity` | none needed: refuses every caller but the collector's own Keycloak client over a bearer (OPS-48, T-1402); projects are checked against the mirror | creates or runs |
| `POST /api/v1/alerts/notices/{}/read` | `api::alerts::read_notice` | none needed: UPDATE ... WHERE id = $1 AND recipient = ANY($2): another caller's id is 404 | creates or runs |
| `POST /api/v1/alerts/{}/mute` | `api::alerts::mute` | none needed: UPDATE ... WHERE id = $1 AND subject = $2: another caller's id is 404 | creates or runs |
| `POST /api/v1/auth/backchannel-logout` | `auth::oidc::backchannel_logout` | none needed: the realm's call: the logout token's signature, issuer and audience are verified, and it must carry the logout event and no nonce | creates or runs |
| `POST /api/v1/auth/logout` | `auth::oidc::logout` | none needed: ends the caller's own session | creates or runs |
| `POST /api/v1/feedback` | `api::feedback::send_feedback` | none needed: anyone signed in may send feedback; bounded text, rate-limited per subject, scrubbed; reading it needs an org administrator | creates or runs |
| `POST /api/v1/mcp` | `mcp::handle_mcp` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/notifications/{}/read` | `api::entity_comments::read_notification` | none needed: mark_read filters by the caller's recipients: another caller's id is 404 | creates or runs |
| `POST /api/v1/organization/people` | `api::people::create_person` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/disable` | `api::people::disable_person` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/enable` | `api::people::enable_person` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/remove-second-factor` | `api::people::remove_second_factor` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/resend-invitation` | `api::people::resend_invitation` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/reset-password` | `api::people::reset_password` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/organization/people/{}/sign-out` | `api::people::sign_out_person` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/preferences/recent` | `api::preferences::add_recent` | none needed: the caller's own preferences, keyed by subject | creates or runs |
| `POST /api/v1/projects` | `api::projects::open_project` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/access/explore` | `api::access_explore::explore_access` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs` | `api::agent_runs::create_run` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/answers` | `api::agent_runs::answer_question` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/cancel` | `api::agent_runs::cancel_run` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/functions/{}` | `api::agent_runs::call_function` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/messages` | `api::agent_runs::post_message` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/preview-errors` | `api::agent_runs::post_preview_error` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/preview-observations` | `api::agent_runs::post_preview_observation` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/agent-runs/{}/publish` | `api::agent_runs::publish_run` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/apps/rename-shapes` | `api::app_shapes::rename_shapes` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/apps/{}/rebuild` | `api::app_build::rebuild` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/apps/{}/restore` | `api::app_build::restore` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/assistant/conversations` | `api::assistant::start_conversation` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/assistant/propose-endpoint` | `api::assistant::propose_endpoint` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/catalogue/drafts` | `api::catalogue_draft::draft_publication` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/changes/{}/approve` | `api::changes::approve_change` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/changes/{}/reject` | `api::changes::reject_change` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/changes/{}/restore` | `api::restore::restore_change` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/changes/{}/undo` | `api::restore::undo_change` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/datamodels/{}/share` | `api::datamodels::share_model` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/drift/{}/{}/adopt` | `api::drift::adopt_drift` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/drift/{}/{}/revert` | `api::drift::revert_drift` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/duplicate` | `api::projects::duplicate_project` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/endpoints/{}/access/simulate` | `api::access_simulate::simulate_access` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/endpoints/{}/preview` | `api::endpoint_preview::preview_endpoint` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/flows` | `api::blueprints::start_flow` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/import` | `api::import::import` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/knowledge/deployments/{}/chat` | `api::knowledge::chat` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/knowledge/sources/{}/files` | `api::knowledge::upload_file` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/knowledge/sources/{}/inclusion` | `api::knowledge::set_inclusion` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/knowledge/sources/{}/recrawl` | `api::knowledge::recrawl` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/ops/{}` | `api::ops::run_op` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/pipelines/test` | `api::pipeline_test::test_pipeline` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/pipelines/{}/rejected/retry` | `api::pipelines::retry_rejected` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/pipelines/{}/runs` | `api::pipelines::post_run` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/pipelinetemplates` | `api::pipeline_templates::create_pipeline_template` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/serviceaccounts/{}/keys` | `api::service_accounts::create_key` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/serviceaccounts/{}/keys/claims/{}` | `api::service_accounts::use_key_claim` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/serviceaccounts/{}/keys/{}/rotate` | `api::service_accounts::rotate_key` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/spaces/{}/comments` | `api::entity_comments::add_comment` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/spaces/{}/move` | `api::space_move::start_move` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/spaces/{}/retention/preview` | `api::space_retention::preview_retention` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/spaces/{}/trash` | `api::entity_trash::keep` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/spaces/{}/views` | `api::data_views::create_view` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/syncsources/{}/detach` | `api::sync_sources::detach` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/syncsources/{}/pause` | `api::sync_sources::pause` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/syncsources/{}/sync` | `api::sync_sources::sync_now` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/workspaces` | `api::workspaces::open_workspace` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/workspaces/{}/preview` | `api::workspaces::start_workspace_preview` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/workspaces/{}/propose` | `api::workspaces::propose_workspace` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/workspaces/{}/update` | `api::workspaces::update_workspace` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/projects/{}/{}` | `api::mutate::create` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/tools/generate` | `tools::model_tools::generate` | none needed: stateless compile of the caller's own source in Model Tools; writes nothing | creates or runs |
| `POST /api/v1/tools/import-sdm` | `tools::model_tools::import_sdm` | none needed: stateless compile of a catalogue id (is_catalogue_id refuses a URL); writes nothing | creates or runs |
| `POST /api/v1/tools/infer-schema` | `tools::model_tools::infer_schema` | none needed: stateless inference over the caller's own sample, parsed in memory by Model Tools; writes nothing | creates or runs |
| `POST /api/v1/webhooks/gitea` | `api::webhook::gitea_webhook` | `permissions` (signed-in user's rights in the project) | creates or runs |
| `POST /api/v1/webhooks/sync/{}/{}` | `api::sync_sources::webhook` | none needed: the body must carry the HMAC of the SyncSource's own secret (MF-44, PF-59) | creates or runs |
| `POST /apps/{}/api/functions/{}` | `apps::functions::call` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | creates or runs |
| `POST /apps/{}/api/services/ai/complete` | `apps::ai::complete` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | creates or runs |
| `POST /apps/{}/api/services/email/send` | `apps::email::send` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | creates or runs |
| `POST /apps/{}/api/services/files/{}` | `apps::files::url` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | creates or runs |
| `POST /internal/agent-runs/events` | `api::internal::agent_runs::internal_post_event` | internal bearer (`auth::internal`) | creates or runs |
| `POST /internal/agent-runs/{}/mcp` | `api::internal::agent_runs::internal_mcp` | internal bearer (`auth::internal`) and `permissions` | creates or runs |
| `POST /internal/model-key` | `api::internal::model_key::model_key` | internal bearer (`auth::internal`) | creates or runs |
| `POST /internal/pipeline-tests/{}` | `api::internal::pipeline_tests::capture` | internal bearer (`auth::internal`) | creates or runs |
| `POST /internal/pipelines/{}/{}/outcomes` | `api::internal::pipeline_rejected::outcomes` | internal bearer (`auth::internal`) | creates or runs |
| `POST /internal/pipelines/{}/{}/rejected` | `api::internal::pipeline_rejected::rejected` | internal bearer (`auth::internal`) | creates or runs |
| `POST /internal/pipelines/{}/{}/samples` | `api::internal::pipeline_rejected::sampled` | internal bearer (`auth::internal`) | creates or runs |
| `POST /live-notify/{}` | `api::live::notify` | none needed: the path key is an HMAC under a per-process secret (live::LiveHub::key); body bounded, a delivery only makes a view read again | creates or runs |
| `POST /mail/unsubscribe` | `apps::email::unsubscribe` | none needed: the link is an HMAC over project, app and subject, each validated before it is echoed or bound | creates or runs |
| `PUT /api/v1/alerts` | `api::alerts::subscribe` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /api/v1/preferences` | `api::preferences::put_preferences` | none needed: the caller's own preferences, keyed by subject | replaces |
| `PUT /api/v1/projects/{}/datamodels/{}/source` | `api::datamodels::put_source` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /api/v1/projects/{}/drafts/{}/{}` | `api::drafts::put_draft` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /api/v1/projects/{}/registry` | `api::projects::repoint_project` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /api/v1/projects/{}/spaces/{}/views/{}` | `api::data_views::update_view` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /api/v1/projects/{}/{}/{}` | `api::mutate::replace` | `permissions` (signed-in user's rights in the project) | replaces |
| `PUT /apps/{}/api/services/files/{}` | `apps::files::put` | App token (`BearerVerifier::verify_app_token`), the App's person and role (`apps::roles::person`) and its declared services (`apps::services::gate`) | replaces |

### 11.3 Functions ranked by risk

3 478 non-test Portal functions. 3 368 are reachable from a route within four calls: the reconciler runs in-process, and the `ops` tables tie most of it to a route. The ranking query is the one in 9.3 with `cc ≥ 30`. The top tier and the GREP patterns:

| Function | cc × routes | Risk read for | Verdict |
|---|---|---|---|
| `api::mutate::propose_engine` | 136 × 7 | a write past the caller's rights | safe: every route into it reaches `permissions::for_request` (route table) |
| `api::changes::approve_change_for` | 35 × 7 | self-approval | safe: `permissions::for_request(…).check` before the merge; an approver named by the author cannot approve their own Change (T-3433) |
| `reconciler::daemon::Syncer::do_sync` | 205 × 4 | reconciling a resource into another project | safe: the reconciler loop runs it on the forge's state; no request value reaches it |
| `ops::views::operations`, `ops::admin::operations`, `ops::proposals::operations`, `ops::people::operations`, `ops::compute::operations` | 30–42 × 8 | operation tables shared by the API and MCP | safe: `ops::listing` filters by the `Caller`, and each operation is checked again when it runs |
| `mcp::dispatch` | 46 × 3 | MCP tools past the caller's rights | safe: `tools/list` and every call go through `ops::listing(&caller, …)` |
| `api::pipelines::scrape` | 32 × 6 | parsing untrusted metrics text | safe: a line parser over the runner's Prometheus text; a malformed line is skipped, counts are summed as `u64` |
| `api::export::export` | 63 × 2 | secrets in the bundle | safe: behind `permissions`; `strip_secret_values` empties literal values and keeps `secretRef`; `status` dropped (MF-17) |
| `api::delete::delete_with_identity` | 70 × 1 | delete past the caller's rights | safe: `effective.check(kind, Verb::Delete, Some(target))` before anything is removed (`delete.rs:433`) |
| `api::import::plan_import` | 46 × 1 | a literal secret coming in | safe: behind `permissions`; the write door refuses a literal secret (`find_literal_secret`, T-2540) |
| `api::basemap::get_tile` | 55 × 1 | path traversal into the tile cache | safe: style fixed to `default`, project must exist, `z`/`x`/`y` parse as integers, extension allowlisted |
| `agents::share::render` | 65 × 1 | injection into the shared manifest | safe: name and space refused unless DNS-1123 labels |
| `agents::oneshot::code_pass::Driver::drive_code`, `conversation::drive_conversation`, `edit_loop::scoped_edit_turn` | 35–77 × 2 | an agent run reaching beyond its workspace | safe: behind `permissions`; the run gets its person's identity, or none and reads no data (ADR-N-038); data through agent-proxy (section 10) |
| `reconciler::streams::StreamDeployer::converge` | 59 × 2 | a secret rendered into a stream | safe: `${VAR}` references compare by escaped pattern (`regex::escape`), values come from `secretRef` |
| `auth::client_auth` realm clients (`people`, `reconciler::groups`, `reconciler::app_clients`) | caller check | credential carry-over | finding F5, fixed |
| GREP: `Regex::new` (12 non-test) | | regex from input | constants, or built from `regex::escape` (`streams.rs:193`, `agents/patch.rs:266`); the `regex` crate matches in linear time |
| GREP: TLS switched off, `unsafe` | | | 0 hits in `src/` |
| GREP: secret compared with `==` | 2 hits | timing | a test, and a Kubernetes `secretName` comparison: no credential |
| GREP: secrets in log calls | 9 hits | | each logs that a token is missing, refused or failed to verify, never its value |

**F5 (fixed in T-3592).** The Portal's Keycloak admin clients in `people.rs`, `reconciler/groups.rs` and `reconciler/app_clients.rs` kept reqwest's default redirect policy, so a redirecting realm token endpoint would have received the client-credentials form, client secret included, again at the host the redirect named. Like F4 it needs a compromised realm or its ingress. `auth::client_auth::realm_http` builds the client with `Policy::none()`; the test `the_realm_client_resends_no_grant_to_the_host_a_redirect_names` answers a token request with 307 to a second server and asserts that server receives nothing.

Zero open priority-1 findings in the Portal.

## Related

- [Security suite](../../tests/security/README.md): the live attack suites this review adds to.
- [Route table](../../security/routes.csv): the gateway routes and the test that sweeps each.
