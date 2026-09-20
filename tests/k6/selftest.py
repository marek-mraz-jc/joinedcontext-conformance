#!/usr/bin/env python3
"""Proof that the T-0065 budgets can go red and that the benchmark actually reads (TS-22, EP-28, OPS-18).

Three k6 runs, no dedicated hardware needed:

1. `selftest.js` with a distribution inside every budget    -> must pass.
2. `selftest.js` with one that misses every budget          -> must fail, naming each of them.
3. the benchmark itself against a stub space in this process, with `--no-thresholds` because the
   timing of a shared machine says nothing about a 5 ms budget -> must discover the entity, read
   through both hops and pass every check.

    python3 selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ENTITY = {"id": "urn:ngsi-ld:AirQualityObserved:selftest:1", "type": "AirQualityObserved"}
# every budget that a synthetic distribution can miss; dropped_iterations is k6's own bookkeeping
BUDGETS = [
    "ep28_gateway_overhead_ms",
    "ops18_gateway_read_ms",
    "ts22_gateway_reads",
    "http_req_failed",
    "checks",
]
ENDURANCE_BUDGETS = [
    "ops18_gateway_read_ms",
    "ts22_gateway_reads",
    "ts22_memory_growth",
    "http_req_failed",
    "checks",
]
ENDURANCE_BAD_BUDGETS = [
    "ts22_memory_growth",
    "http_req_failed",
    "checks",
]
EXPORT_BUDGETS = [
    "ep44_export_ttfb_ms",
    "ep44_export_ms",
    "ep41_export_rows",
    "ep44_export_timeouts",
    "http_req_failed",
    "checks",
]
EXPORT_BAD_BUDGETS = [
    "ep44_export_ttfb_ms",
    "ep44_export_ms",
    "ep41_export_rows",
    "ep44_export_timeouts",
]
ENDPOINT_BUDGETS = [
    "ep28_endpoint_overhead_ms",
    "ts05_endpoint_read_ms",
    "ts05_endpoint_reads",
    "ep05_geojson_ttfb_ms",
    "ep20_limit_headers_missing",
    "ep20_limit_shed",
    "checks",
]
ENDPOINT_BAD_BUDGETS = ENDPOINT_BUDGETS

# The stub endpoint's own token bucket (EP-20): a steady 60/s with 120 in reserve. The wiring
# run stays under it while it measures and deliberately exceeds it afterwards, which is the
# only way a run can tell a limiter from a missing one.
LIMIT_PER_MINUTE = 3600
LIMIT_BURST = 120


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    scrape_count = 0
    tokens = float(LIMIT_BURST)
    refilled_at = 0.0
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    @classmethod
    def spend(cls):
        """One token of the endpoint bucket, and what the RateLimit field should say."""
        with cls.lock:
            now = time.monotonic()
            if cls.refilled_at == 0.0:
                cls.refilled_at = now
            cls.tokens = min(
                float(LIMIT_BURST),
                cls.tokens + (now - cls.refilled_at) * (LIMIT_PER_MINUTE / 60.0),
            )
            cls.refilled_at = now
            allowed = cls.tokens >= 1.0
            if allowed:
                cls.tokens -= 1.0
            return allowed, int(cls.tokens)

    def rate_limited(self) -> bool:
        """Answers 429 with the standard field when the endpoint bucket is spent."""
        allowed, remaining = Stub.spend()
        headers = [
            ("ratelimit-limit", str(LIMIT_PER_MINUTE)),
            ("ratelimit-remaining", str(remaining)),
            ("ratelimit-reset", "1"),
        ]
        if allowed:
            self.extra_headers = headers
            return False
        body = json.dumps({"type": "too-many-requests", "status": 429}).encode()
        self.send_response(429)
        for name, value in headers + [("retry-after", "1")]:
            self.send_header(name, value)
        self.send_header("Content-Type", "application/problem+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        self.extra_headers = []
        # Only the endpoint surface carries a limit; the space surface the other benchmarks
        # read must never be shed, or their budgets would measure this stub instead.
        if "/api/endpoint/" in path and self.rate_limited():
            return
        if path.endswith("/types"):
            body = json.dumps(
                {
                    "id": "urn:ngsi-ld:EntityTypeList:selftest",
                    "type": "EntityTypeList",
                    "typeList": [ENTITY["type"]],
                }
            ).encode()
            self.send_response(200)
            for name, value in self.extra_headers:
                self.send_header(name, value)
            self.send_header("Content-Type", "application/ld+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.endswith("/file.geojson"):
            body = json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": ENTITY["id"],
                            "geometry": {"type": "Point", "coordinates": [19.15, 48.73]},
                            "properties": {"type": ENTITY["type"]},
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            for name, value in self.extra_headers:
                self.send_header(name, value)
            self.send_header("Content-Type", "application/geo+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.endswith("/file.csv"):
            rows = ["id,type,val"] + [f"urn:ngsi-ld:Item:{i},Item,{i}" for i in range(200)]
            body = ("\n".join(rows) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.endswith("/file.zip"):
            body = b"PK\x03\x04" + (b"\x00" * 2048)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif "/metrics" in path:
            with Stub.lock:
                Stub.scrape_count += 1
                count = Stub.scrape_count
            if "leak" in path:
                rss_bytes = 100 * 1024 * 1024 + count * 10 * 1024 * 1024
            else:
                rss_bytes = 100 * 1024 * 1024 + (count % 2) * 200 * 1024
            body = (
                f"# TYPE process_resident_memory_bytes gauge\n"
                f"process_resident_memory_bytes {rss_bytes}\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = json.dumps([ENTITY] if path.endswith("/entities") else ENTITY).encode()
            self.send_response(200)
            for name, value in self.extra_headers:
                self.send_header(name, value)
            self.send_header("Content-Type", "application/ld+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def serve() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def k6(args: list[str], env: dict[str, str], workdir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        env={**os.environ, **env},
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="jc-k6-selftest-")
    failures = []

    budget_run = ["k6", "run", "--quiet", os.path.join(HERE, "selftest.js")]
    good = k6(budget_run, {"JC_K6_CASE": "good"}, workdir)
    if good.returncode != 0:
        failures.append(f"a gateway inside every budget failed the run:\n{good.stdout}\n{good.stderr}")
    for budget in BUDGETS:
        if f"PASS {budget}" not in good.stdout:
            failures.append(f"{budget} was not evaluated on the conforming distribution:\n{good.stdout}")

    bad = k6(budget_run, {"JC_K6_CASE": "bad"}, workdir)
    if bad.returncode == 0:
        failures.append(f"a gateway outside every budget passed the run:\n{bad.stdout}")
    for budget in BUDGETS:
        if f"FAIL {budget}" not in bad.stdout:
            failures.append(f"{budget} did not fail on a distribution that misses it:\n{bad.stdout}")

    gateway_port, broker_port = serve(), serve()
    # through run.sh, so the entrypoint of the suite is part of what is proven
    latency_dir = os.path.join(workdir, "latency")
    os.makedirs(latency_dir, exist_ok=True)
    benchmark = k6(
        ["bash", os.path.join(HERE, "run.sh"), "--quiet", "--no-thresholds"],
        {
            "GATEWAY_URL": f"http://127.0.0.1:{gateway_port}/ngsi-ld/v1",
            "BROKER_URL": f"http://127.0.0.1:{broker_port}/ngsi-ld/v1",
            "JC_REPORTS_DIR": latency_dir,
            "JC_K6_RATE": "20",
            "JC_K6_DURATION": "5s",
            "JC_K6_VUS": "5",
            "JC_K6_OVERHEAD_RATE": "10",
            "JC_K6_WARMUP_READS": "3",
        },
        workdir,
    )
    if benchmark.returncode != 0:
        failures.append(f"the benchmark did not run against a stub space:\n{benchmark.stdout}\n{benchmark.stderr}")
    else:
        metrics = json.load(open(os.path.join(latency_dir, "summary.json")))["metrics"]
        if metrics.get("checks", {}).get("values", {}).get("rate") != 1.0:
            failures.append(f"a check failed against a conforming stub space: {metrics.get('checks')}")
        for name in ("ts22_gateway_reads", "ep28_gateway_overhead_ms", "ep28_broker_read_ms"):
            if name not in metrics:
                failures.append(f"the benchmark produced no {name} sample, so it measured nothing")

    # 4. the four new selftest.js cases
    for case, budgets_list in [
        ("endurance-good", ENDURANCE_BUDGETS),
        ("export-good", EXPORT_BUDGETS),
    ]:
        res = k6(budget_run, {"JC_K6_CASE": case}, workdir)
        if res.returncode != 0:
            failures.append(f"case {case} failed unexpectedly:\n{res.stdout}\n{res.stderr}")
        for budget in budgets_list:
            if f"PASS {budget}" not in res.stdout:
                failures.append(f"{budget} was not evaluated in {case}:\n{res.stdout}")

    for case, bad_budgets in [
        ("endurance-bad", ENDURANCE_BAD_BUDGETS),
        ("export-bad", EXPORT_BAD_BUDGETS),
    ]:
        res = k6(budget_run, {"JC_K6_CASE": case}, workdir)
        if res.returncode == 0:
            failures.append(f"case {case} passed unexpectedly:\n{res.stdout}")
        for budget in bad_budgets:
            if f"FAIL {budget}" not in res.stdout:
                failures.append(f"{budget} did not fail in {case}:\n{res.stdout}")

    # 5. wiring run of gateway-endurance.js (flat and leaking)
    endurance_flat_dir = os.path.join(workdir, "endurance-flat")
    os.makedirs(endurance_flat_dir, exist_ok=True)
    with Stub.lock:
        Stub.scrape_count = 0
    endurance_flat = k6(
        ["bash", os.path.join(HERE, "run.sh"), "--quiet", "--no-thresholds"],
        {
            "JC_K6_SCRIPT": "gateway-endurance.js",
            "GATEWAY_URL": f"http://127.0.0.1:{gateway_port}/ngsi-ld/v1",
            "GATEWAY_METRICS_URL": f"http://127.0.0.1:{gateway_port}/metrics",
            "JC_REPORTS_DIR": endurance_flat_dir,
            "JC_K6_DURATION": "8s",
            "JC_K6_PROBE_INTERVAL": "1s",
            "JC_K6_WARMUP": "0s",
            "JC_K6_MEMORY_WINDOW": "8s",
            "JC_K6_RATE": "20",
            "JC_K6_VUS": "5",
            "JC_K6_WARMUP_READS": "2",
        },
        workdir,
    )
    if endurance_flat.returncode != 0:
        failures.append(f"flat endurance run failed:\n{endurance_flat.stdout}\n{endurance_flat.stderr}")
    else:
        flat_metrics = json.load(open(os.path.join(endurance_flat_dir, "summary.json")))["metrics"]
        # a k6 trend carries avg/min/med/max, not a sample count: its presence with a max is the proof
        if flat_metrics.get("ts22_gateway_rss_mb", {}).get("values", {}).get("max", 0) <= 0:
            failures.append(f"flat endurance run scraped no resident memory: {flat_metrics.get('ts22_gateway_rss_mb')}")
        if "ts22_rss_slope_pct" not in flat_metrics:
            failures.append("flat endurance run recorded no ts22_rss_slope_pct")
        if flat_metrics.get("ts22_memory_growth", {}).get("values", {}).get("count") != 0:
            failures.append(f"flat endurance run memory growth count != 0: {flat_metrics.get('ts22_memory_growth')}")

    endurance_leak_dir = os.path.join(workdir, "endurance-leak")
    os.makedirs(endurance_leak_dir, exist_ok=True)
    with Stub.lock:
        Stub.scrape_count = 0
    endurance_leak = k6(
        ["bash", os.path.join(HERE, "run.sh"), "--quiet", "--no-thresholds"],
        {
            "JC_K6_SCRIPT": "gateway-endurance.js",
            "GATEWAY_URL": f"http://127.0.0.1:{gateway_port}/ngsi-ld/v1",
            "GATEWAY_METRICS_URL": f"http://127.0.0.1:{gateway_port}/metrics/leak",
            "JC_REPORTS_DIR": endurance_leak_dir,
            "JC_K6_DURATION": "8s",
            "JC_K6_PROBE_INTERVAL": "1s",
            "JC_K6_WARMUP": "0s",
            "JC_K6_MEMORY_WINDOW": "8s",
            "JC_K6_RATE": "20",
            "JC_K6_VUS": "5",
            "JC_K6_WARMUP_READS": "2",
        },
        workdir,
    )
    if endurance_leak.returncode != 0:
        failures.append(f"leaking endurance run failed execution:\n{endurance_leak.stdout}\n{endurance_leak.stderr}")
    else:
        leak_metrics = json.load(open(os.path.join(endurance_leak_dir, "summary.json")))["metrics"]
        leak_growth = leak_metrics.get("ts22_memory_growth", {}).get("values", {}).get("count", 0)
        if leak_growth < 1:
            failures.append(f"leaking endurance run memory growth count {leak_growth} < 1")

    # 6. wiring run of bulk-export.js
    export_dir = os.path.join(workdir, "export")
    os.makedirs(export_dir, exist_ok=True)
    export_run = k6(
        ["bash", os.path.join(HERE, "run.sh"), "--quiet", "--no-thresholds"],
        {
            "JC_K6_SCRIPT": "bulk-export.js",
            "EXPORT_URL": f"http://127.0.0.1:{gateway_port}",
            "EXPORT_ROWS": "200",
            "JC_K6_EXPORT_STREAMS": "2",
            "JC_REPORTS_DIR": export_dir,
        },
        workdir,
    )
    if export_run.returncode != 0:
        failures.append(f"bulk-export run failed:\n{export_run.stdout}\n{export_run.stderr}")
    else:
        export_metrics = json.load(open(os.path.join(export_dir, "summary.json")))["metrics"]
        if export_metrics.get("checks", {}).get("values", {}).get("rate") != 1.0:
            failures.append(f"bulk-export checks failed: {export_metrics.get('checks')}")
        rows = export_metrics.get("ep41_export_rows", {}).get("values", {}).get("count", 0)
        if rows < 400:
            failures.append(f"bulk-export rows count {rows} < 400")

    # 7. the endpoint budgets on synthetic samples, and a wiring run of the benchmark itself
    for case, budgets_list, must_pass in [
        ("endpoint-good", ENDPOINT_BUDGETS, True),
        ("endpoint-bad", ENDPOINT_BAD_BUDGETS, False),
    ]:
        res = k6(budget_run, {"JC_K6_CASE": case}, workdir)
        if must_pass and res.returncode != 0:
            failures.append(f"case {case} failed unexpectedly:\n{res.stdout}\n{res.stderr}")
        if not must_pass and res.returncode == 0:
            failures.append(f"case {case} passed unexpectedly:\n{res.stdout}")
        want = "PASS" if must_pass else "FAIL"
        for budget in budgets_list:
            if f"{want} {budget}" not in res.stdout:
                failures.append(f"{budget} was not {want} in {case}:\n{res.stdout}")

    endpoint_dir = os.path.join(workdir, "endpoint")
    os.makedirs(endpoint_dir, exist_ok=True)
    with Stub.lock:
        Stub.tokens = float(LIMIT_BURST)
        Stub.refilled_at = 0.0
    endpoint_run = k6(
        ["bash", os.path.join(HERE, "run.sh"), "--quiet", "--no-thresholds"],
        {
            "JC_K6_SCRIPT": "transport_endpoint_load.js",
            "ENDPOINT_URL": f"http://127.0.0.1:{gateway_port}/api/endpoint/selftest",
            "BROKER_URL": f"http://127.0.0.1:{broker_port}/ngsi-ld/v1",
            "JC_REPORTS_DIR": endpoint_dir,
            "JC_K6_RATE": "20",
            "JC_K6_DURATION": "5s",
            "JC_K6_VUS": "5",
            "JC_K6_GEOJSON_RATE": "2",
            "JC_K6_OVERHEAD_RATE": "5",
            "JC_K6_WARMUP_READS": "3",
            # deliberately far above the stub's 60/s, so the limiter has to shed
            "JC_K6_BURST_RATE": "400",
            "JC_K6_BURST_DURATION": "3s",
        },
        workdir,
    )
    if endpoint_run.returncode != 0:
        failures.append(f"the endpoint benchmark did not run against a stub endpoint:\n{endpoint_run.stdout}\n{endpoint_run.stderr}")
    else:
        endpoint_metrics = json.load(open(os.path.join(endpoint_dir, "summary.json")))["metrics"]
        for name in ("ts05_endpoint_reads", "ep28_endpoint_overhead_ms", "ep05_geojson_ttfb_ms"):
            if name not in endpoint_metrics:
                failures.append(f"the endpoint benchmark produced no {name} sample, so it measured nothing")
        # the discovery hop, the read hop and the download all answered as expected
        if endpoint_metrics.get("checks", {}).get("values", {}).get("rate") != 1.0:
            failures.append(f"a check failed against a conforming stub endpoint: {endpoint_metrics.get('checks')}")
        # EP-20: a burst four hundred a second past a sixty a second bucket has to be shed,
        # and nothing may be shed while the sustained phase is the only load
        shed = endpoint_metrics.get("ep20_limit_shed", {}).get("values", {}).get("count", 0)
        if shed < 1:
            failures.append(f"the burst was never shed by the stub's rate limiter: count {shed}")
        missing = endpoint_metrics.get("ep20_limit_headers_missing", {}).get("values", {}).get("count", 0)
        if missing != 0:
            failures.append(f"{missing} answers arrived without the standard RateLimit field")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: every budget passes a conforming gateway, fails a slow one, and all benchmarks run against stubs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
