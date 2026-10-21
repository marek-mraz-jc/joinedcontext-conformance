# Standards suite on demand

`run.sh` runs the chosen suites against the chosen Endpoints of dev or prod and writes one verdict
per (Endpoint, suite) to `results.tsv`; `summary.py` renders it as the run page table. The workflow
`.github/workflows/standards.yml` starts it from the Actions tab; the inputs, the suites and the
safety rules (prod anonymous, read-only, paced; dev writes only on a dev write Endpoint with the CI
service account) are in the top-level README, section 5, "Run the standards suite".
