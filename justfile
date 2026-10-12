# The live checks against dev; everything else runs through bin/jc-conformance.

# The Helsinki buses against HSL's feed for five minutes (T-3907); needs the dev kubeconfig.
helsinki-live *args:
    bash -c '. tests/dev.env.sh helsinki-live >&2 && exec tests/helsinki-live/run.sh "$@"' _ {{args}}
