# JoinedContext Portal Playwright E2E Harness (TS-12, TS-13)

Browser end-to-end user journeys for the JoinedContext Portal UI (React 19 / axum) and Keycloak OIDC authentication.

## Environment Variables

- `BASE_URL`: Root URL of the deployed portal (default: `https://2.28.67.127.sslip.io`).
- `PORTAL_USER` / `PORTAL_PASSWORD`: steward credentials. No default — an unset variable fails the run with the variable name rather than pretending the portal is broken.
- `PORTAL_VIEWER_USER` / `PORTAL_VIEWER_PASSWORD`: the read-only account (`demo.viewer`).
- `PORTAL_INVITE_URL` / `PORTAL_INVITE_PASSWORD`: Single-use invitation link and initial password for Journey 1 onboarding. When unset, the invite onboarding test is skipped cleanly.
- `PORTAL_APPROVER_USER` / `PORTAL_APPROVER_PASSWORD`: the domain approver of Journey 3. Separation of duties (CC-34) means this is a different person than `PORTAL_USER`.
- `PORTAL_BLUEPRINT`: blueprint the flow journey instantiates (default `Threshold Alert`).
- `PORTAL_SENSOR_LABEL`: label of an entity the steward may read, picked in the generated form.
- `PORTAL_FORBIDDEN_ENTITY_LABEL`: label of an entity outside the steward's grant; the entity picker must never offer it. Unset skips that check.
- `PORTAL_DASHBOARD`: spatial dashboard the map journey opens (default `Air Quality`). Its default viewport must centre on a feature, because journey 4 clicks the middle of the map to open a feature popup.
- `PORTAL_MAP_LAYER`: name of the vector layer switched off and on in the layer control (default `Sensors`).
- `PORTAL_DENSE_DASHBOARD`: dashboard whose layer carries at least 50 000 features, the one UI-21 sends through deck.gl. Unset skips journey 5 — a missing dataset is not a defect of the portal.
- `PORTAL_PRIVATE_DASHBOARD`: dashboard bound to an endpoint that is not `audience: public`. Journey 4 replays its GeoJSON request from an anonymous browser context and expects 401, 403 or 404. Unset skips that one test.
- `PORTAL_DRIFTED_FLOW`: flow that has been changed out of band and must show the `Drifted` chip. Unset skips journey 6: drift cannot be produced from a browser.
- `PORTAL_LIVE_FLOW`: converged flow that must offer no Revert or Adopt button. Unset skips the negative drift test.
- `PORTAL_PIPELINE`: the running pipeline journey 9 watches (default `aq-mqtt-ingest`). Its counter must move while the journey runs; a view showing "no numbers" means the portal has no runner URL configured.
- `PORTAL_PUBLIC_DASHBOARD_URL`: full URL of a published public dashboard. Journey 13 opens it in an anonymous context to prove it needs no session; unset skips that one test.
- `PORTAL_ENDPOINT`: the published endpoint journeys 8 and 11 use (default `public-air`). Journey 11 selects it in the Data Access view's own endpoint control and reads the slug from there, so the matrix it checks belongs to the endpoint it then calls.
- `PORTAL_PROJECT`: project the drift journey writes in (default `banskabystrica`).
- `PORTAL_DRIFT_RESOURCE`: the resource journey 14 drifts on purpose (default `ovzdusie`).
- `JC_DRIFT_TOKEN`: bearer token of the scoped ServiceAccount that writes that drift. It is scoped to the one resource and is never an admin credential; an unset variable fails journey 14 with the variable name rather than skipping it.
- `JC_REPORTS_DIR`: Target folder for JUnit, HTML, and artifact test reports.
- `E2E_ALL_BROWSERS`: If set, runs Chromium, Firefox, and WebKit; otherwise runs Chromium only.

## Running Tests

Tests navigate solely via accessible user-visible controls (`getByRole`, `getByLabel`) without shortcut URL mutations:

```bash
# from the repository root, with the harness dependencies installed (npm ci)
BASE_URL=https://<host> ./node_modules/.bin/playwright test --config e2e/playwright.config.ts smoke.spec.ts

# from the runner image, which carries Chromium and the harness
docker run --rm -e BASE_URL=https://<host> -e PORTAL_USER -e PORTAL_PASSWORD \
  -v "$PWD/reports:/reports" ghcr.io/marek-mraz/joinedcontext-conformace:main playwright
```

Journeys 2 and 3 are a chain: journey 2 leaves a change proposal pending, journey 3 approves it. Journey 6 is a chain of its own: adopting the drift leaves a pending proposal that the same file then reads, so its tests run in file order.
Run them in file order (the default) against the same portal, or journey 3 skips for want of
anything to approve. The WCAG audit in `accessibility/` writes one JSON report per view into
`$JC_REPORTS_DIR/a11y/` (TS-13).

`.github/workflows/e2e.yml` runs the same command nightly and on dispatch, with the credentials
as repository secrets. It never runs on push: dev is one shared workbench and DEMO.md is demoed
from it. A preflight job checks the demo project, the credentials and that the portal answers, so
a deployment that is down fails the lane in seconds with that sentence instead of forty minutes
of timeouts. The journeys then run in four shards (`flows`, `endpoints`, `views`,
`accessibility`) split along the chains rather than by count, and each shard uploads its html
report with the retained traces and videos as `e2e-report-<shard>`. A dispatch may name one spec;
only the shard that owns it runs. The fast CI lane only type-checks the harness and lists the
specs, so a broken harness is caught on every commit without needing a deployment.
