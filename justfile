# The live checks against dev; everything else runs through bin/jc-conformance.

# The Helsinki buses against HSL's feed for five minutes (T-3907); needs the dev kubeconfig.
helsinki-live *args:
    bash -c '. tests/dev.env.sh helsinki-live >&2 && exec tests/helsinki-live/run.sh "$@"' _ {{args}}

# The App sharing matrix on dev (T-4911): five visibilities x six people x every door, with
# screenshots, into reports/app-sharing/report.html; needs the dev kubeconfig for the passwords.
app-sharing *args:
    bash -c '. tests/dev.env.sh app-sharing >&2 && JC_REPORTS_DIR="${JC_REPORTS_DIR:-reports}" exec ./node_modules/.bin/playwright test --config e2e/playwright.config.ts e2e/journeys/39-app-sharing-matrix.spec.ts "$@"' _ {{args}}
