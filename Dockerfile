# joinedcontext conformance runner (T-0053, TS-05 TS-09 TS-12 TS-22).
# One image for every suite: Robot Framework (ETSI NGSI-LD), schemathesis, k6,
# Playwright with Chromium, pySHACL. Base images pinned by digest, every package
# verified by hash, runs as a non-root user.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS node

# k6 is built from source rather than taken from the release tarball: the published binary is
# linked against an older Go, so trivy reports every Go stdlib advisory fixed since. `go install`
# verifies each module against the Go checksum database.
FROM golang:1-bookworm@sha256:648f440f42a0958804efb24df176f806f9d353b41f1c0627f666428e40310f6b AS k6
ARG K6_VERSION=2.2.0
# k6 2.2.0 pins google.golang.org/grpc 1.83.0 (CVE-2026-84445, fixed in 1.83.2). A throwaway
# module requiring both lets minimum version selection build k6 against the fixed module;
# drop the extra `go get` the day k6 ships a release that pins it itself.
ARG GRPC_GO_VERSION=1.83.2
ENV CGO_ENABLED=0
WORKDIR /src
RUN go mod init k6build \
 && go get go.k6.io/k6/v2@v${K6_VERSION} google.golang.org/grpc@v${GRPC_GO_VERSION} \
 && go build -o /go/bin/k6 go.k6.io/k6/v2

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    NODE_PATH=/opt/js/node_modules \
    PATH=/opt/js/node_modules/.bin:$PATH \
    JC_TESTS_DIR=/tests \
    JC_REPORTS_DIR=/reports

# `upgrade` too: the pinned base digest lags the Debian security archive (libpcre2 in 12.15),
# and trivy fails the build on any fixed HIGH left in the layer.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates curl git jq \
 && rm -rf /var/lib/apt/lists/*

# node + npm grafted in from the pinned node image (same bookworm base)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY --from=k6 /go/bin/k6 /usr/local/bin/k6

# python suites: hash-pinned lock, no transitive drift
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# browser suites: npm ci verifies every package against the lock integrity hash. npm itself is
# dropped afterwards: nothing at runtime uses it, and its bundled tar/brace-expansion carry
# HIGH/CRITICAL advisories the image would otherwise inherit. Add a JS dependency by editing
# package.json and rebuilding, not by installing inside a running container.
COPY package.json package-lock.json /opt/js/
RUN cd /opt/js && npm ci --omit=dev --no-audit --no-fund \
 && ./node_modules/.bin/playwright install --with-deps chromium \
 && chmod -R a+rX /opt/playwright /opt/js \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /root/.npm

COPY bin/jc-conformance /usr/local/bin/jc-conformance
COPY tests/ /tests/
COPY e2e/ /e2e/

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin testrunner \
 && mkdir -p /reports \
 && chown testrunner:testrunner /reports

USER testrunner
WORKDIR /tests
ENTRYPOINT ["/usr/local/bin/jc-conformance"]
CMD ["--help"]
