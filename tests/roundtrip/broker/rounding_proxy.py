#!/usr/bin/env python3
"""The selftest's deliberate break: a proxy in front of the broker whose OData answers round pm10
to an integer (a surface rounding a float). Everything else passes through; /ws answers 404.

    rounding_proxy.py <listen port> <broker base URL>
"""
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, BROKER = int(sys.argv[1]), sys.argv[2].rstrip("/")


def rounded(node):
    if isinstance(node, dict):
        return {k: round(v) if k == "pm10" and isinstance(v, float) else rounded(v) for k, v in node.items()}
    return [rounded(x) for x in node] if isinstance(node, list) else node


class Proxy(BaseHTTPRequestHandler):
    def forward(self):
        if self.path.endswith("/ws"):
            return self.answer(404, {"Content-Type": "application/json"}, b"{}")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        keep = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
        req = urllib.request.Request(BROKER + self.path, data=body, method=self.command, headers=keep)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, headers, raw = r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            status, headers, raw = e.code, dict(e.headers), e.read()
        if "/odata/" in self.path and "json" in headers.get("Content-Type", "") and raw:
            raw = json.dumps(rounded(json.loads(raw))).encode()
        self.answer(status, headers, raw)

    def answer(self, status, headers, raw):
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = forward

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), Proxy).serve_forever()
