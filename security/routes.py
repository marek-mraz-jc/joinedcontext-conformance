#!/usr/bin/env python3
"""The route and MCP tool inventory of the platform (T-3832): every HTTP route and MCP tool of
the Portal, the platform crates and Antares, and the test that holds its authorization.

    JC_CODE_DIR=/workspace python3 security/routes.py          # rewrite security/routes.csv
    JC_CODE_DIR=/workspace python3 security/routes.py --check  # the code's set is the file's
    python3 security/routes.py --check                         # every row names its test

A row's `authz` is the test that calls the route (or tool) as each kind of caller, or `task:T-n`,
the task that adds that test. A row with neither fails `--check`, and so does a route the code
serves with no row, or a row the code no longer serves (when JC_CODE_DIR is set).

The routes are read from the source, not from the cg graph: the graph has no HTTP channel for
the Portal or the platform crates, and Antares nests its surfaces under prefixes it reads at run
time. So a path is the one the source writes, with the prefix of its surface where the crate
states one (`fn prefix`, `PREFIX`, `VIEW_ROOT`), and `{name}` where the source passes a value
this script does not resolve; `source` names the file and line.
"""

import csv
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
CSV = HERE / "routes.csv"
FIELDS = ["repo", "kind", "method", "route", "source", "authz"]

# (repo, the start of the source path or of the route, authz). The first match decides a row.
# `authz` is a test (repo and file), `task:T-n` (the task that adds the test), or `public: why`
# for a route that answers everyone on purpose and returns nothing a caller could misuse.
AP = "platform crates/agent-proxy/tests/"
COVERAGE = [
    # The matrix is checked against the router: a route without a row fails it (T-2797).
    ("portal", "tests/authz_matrix.yaml", "joinedcontext-portal tests/authz_matrix_tests.rs"),
    # The gate's cases sweep ops::registry(): no binding, another project, a viewer (T-2113).
    ("portal", "src/ops/", "joinedcontext-portal tests/edge_ops_gate_tests.rs"),
    # Every route of the API root is a matrix cell or a listed non-operation route (T-3500),
    # sent through view `all` and view `restricted`; every `mcp: supported` cell is a tool of
    # tools/list (T-3656); the OData and WebSocket columns go through both views.
    ("antares", "/ngsi-ld/v1", "antares crates/antares-broker/tests/operation_matrix.rs"),
    ("antares", "crates/antares-mcp/", "antares crates/antares-broker/tests/operation_matrix.rs"),
    ("antares", "crates/antares-odata/", "antares crates/antares-broker/tests/operation_matrix.rs"),
    ("antares", "crates/odata-axum/", "antares crates/antares-broker/tests/operation_matrix.rs"),
    ("antares", "crates/antares-views/", "antares crates/antares-broker/tests/operation_matrix.rs"),
    ("antares", "crates/antares-ws/", "antares crates/antares-broker/tests/operation_matrix.rs"),
    # Per policy and per surface, 46 scenarios (T-3777, not on dev yet).
    ("antares", "crates/antares-ogc/", "task:T-3777"),
    ("antares", "crates/antares-linkml/", "task:T-3777"),
    ("antares", "addons/joinedcontext-policy/", "antares addons/joinedcontext-policy/tests/access.rs"),
    ("antares", "/q/policy", "antares crates/antares-ops/tests/policy_documents.rs"),
    ("antares", "/q/views", "task:T-3855"),
    ("antares", "/q/schemas", "task:T-3855"),
    ("antares", "/q/grants", "task:T-3855"),
    ("antares", "/q/", "antares crates/antares-api/tests/admin_auth.rs"),
    # Peer-facing, its one credential the mapped subscriptionId (operation-matrix.yaml).
    ("antares", "/ex/v1/remote-notify", "task:T-3588"),
    ("platform", "/healthz", "public: liveness probe, answers ok"),
    ("platform", "/readyz", "public: readiness probe, answers ok"),
    ("platform", "/v1/data/", AP + "edge_routes_data_handler_tests.rs"),
    ("platform", "/v1/forge/", AP + "edge_routes_forge_handler_tests.rs"),
    ("platform", "/v1/fetch", AP + "edge_routes_fetch_handler_tests.rs"),
    ("platform", "/v1/llm/", AP + "edge_routes_llm_handler_tests.rs"),
    ("platform", "/v1/packages/", AP + "edge_routes_packages_handler_tests.rs"),
    ("platform", "/v1/diagnostics/", AP + "edge_routes_diagnostics_handler_tests.rs"),
    ("platform", "/v1/mcp", AP + "edge_routes_mcp_handler_tests.rs"),
    ("platform", "/v1/runs/events", AP + "edge_routes_events_handler_tests.rs"),
    ("platform", "/v1/runs/inbox", AP + "edge_routes_inbox_handler_tests.rs"),
    ("platform", "/internal/runs/", AP + "edge_delegation_tests.rs"),
    # Every /internal route read from the router source, each method, four kinds of stranger (T-3856).
    ("platform", "/internal/v1/projects/", "platform crates/assistant/tests/admin_tests.rs"),
    ("platform", "/d/widget.", "public: the chat widget's script and style, the same for everyone"),
    ("platform", "/d/{public_id}/widget", "platform crates/assistant/tests/chat_tests.rs"),
    ("platform", "/api/v1/d/", "platform crates/assistant/tests/chat_tests.rs"),
    ("platform", "/invoke", "platform crates/functions/tests/invoke_tests.rs"),
    ("platform", "/token", "platform crates/token-sidecar/tests/sidecar_tests.rs"),
    ("platform", "/apps/", "platform crates/wasm-host/tests/host_tests.rs"),
    # The admin port alone serves them; the apps port the edge reaches answers 404 (T-3857).
    ("platform", "crates/wasm-host/", "platform crates/wasm-host/tests/host_tests.rs"),
    ("platform", "crates/context-gateway/", "task:T-3858"),
]

# Files whose routes another file nests, where the crate states no prefix of its own.
MOUNTS = {
    "crates/antares-ops/src/views.rs": "/q",
    "crates/antares-ops/src/views/preview.rs": "/q",
    "crates/antares-ops/src/views/rule_documents.rs": "/q",
    "crates/antares-icsr/src/grant.rs": "/q",
}

RUST_ROUTE = re.compile(r"\.(route|route_service|nest|nest_service)\(")
METHOD = re.compile(r"(?<![A-Za-z0-9_])(get|post|put|patch|delete|head|options|any)(?:_service)?\(")
FILTER = re.compile(r"MethodFilter::([A-Z]+)")
HYPER = re.compile(r'path\s*==\s*"(/[^"]*)"|path\.strip_prefix\("(/[^"]+/)"\)|^\s*"(/[^"]*)"\s*=>', re.M)
TEST_MOD = re.compile(r"#\[cfg\((?:all\()?test\b[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*mod\s")


def repo_dir(code, *names):
    for name in names:
        if (code / name).is_dir():
            return code / name
    raise SystemExit(f"none of {names} under {code}")


def args(src, at):
    """The top-level arguments of the call whose `(` is at `at`, and where it closes."""
    depth, quote, start, out = 0, None, at + 1, []
    i = at
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c == '"':
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                out.append(src[start:i].strip())
                return out, i
        elif c == "," and depth == 1:
            out.append(src[start:i].strip())
            start = i + 1
        i += 1
    return out, i


def without_tests(src):
    """The file without its test modules and its comments (doc examples hold routes too)."""
    m = TEST_MOD.search(src)
    src = src[: m.start()] if m else src
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def strings(src):
    """`const NAME: &str = "v"` and `let NAME = "v"` of a file."""
    found = dict(re.findall(r'const\s+([A-Z_][A-Z0-9_]*)\s*:\s*&(?:\'static\s+)?str\s*=\s*"([^"]*)"', src))
    found.update(re.findall(r'let\s+([a-z_][a-z0-9_]*)\s*=\s*"([^"]*)"\s*;', src))
    return found


def path_of(expr, known):
    expr = expr.strip()
    if expr.startswith('"'):
        return expr.strip('"')
    name = expr.lstrip("&").split("::")[-1]
    return known.get(name, "{" + name + "}")


def methods(expr):
    if expr.split("(")[0].strip().isidentifier() and "(" not in expr:
        return ["*"]
    found = sorted({m.upper() for m in METHOD.findall(expr)} | set(FILTER.findall(expr)))
    return found or ["*"]


PREFIX_FN = re.compile(r"fn\s+prefix\(&self\)\s*->\s*&(?:'static\s+)?str\s*\{\s*(\"[^\"]+\"|[A-Za-z_:]+)\s*\}")
PREFIX_CONST = re.compile(r'const\s+(?:PREFIX|VIEW_ROOT)\s*:\s*&str\s*=\s*"([^"]+)"')


def mount_of(crate_src):
    """The prefix a crate's surface is nested under, when one of its files states it."""
    for f in sorted(crate_src.rglob("*.rs")):
        src = without_tests(f.read_text())
        known = strings(src)
        m = PREFIX_FN.search(src)
        if m:
            return path_of(m.group(1), known)
        m = PREFIX_CONST.search(src)
        if m:
            return m.group(1)
    return ""


def prefixes(src, known):
    """Where each `fn prefix` of a file starts, and what it answers: a route takes the last one
    before it, as a surface's `router` follows its `prefix` in one `impl`."""
    return [(m.start(), path_of(m.group(1), known)) for m in PREFIX_FN.finditer(src)]


def rust_routes(repo, root, crate_src, mount):
    rows = []
    for f in sorted(crate_src.rglob("*.rs")):
        rel = f.relative_to(root).as_posix()
        if "/tests/" in rel or "/examples/" in rel or "/benches/" in rel or rel.endswith("_tests.rs"):
            continue
        src = without_tests(f.read_text())
        known = strings(src)
        own = prefixes(src, known)
        if "axum" not in src:
            # A server on hyper alone matches the path by hand (wasm-host).
            for m in HYPER.finditer(src):
                if m.group(3) and "hyper" not in src:
                    continue  # a `"/x" =>` arm is a route only in a hyper server
                line = src.count("\n", 0, m.start()) + 1
                path = m.group(1) or m.group(3) or m.group(2) + "{*rest}"
                rows.append([repo, "http", "*", mount + path, f"{rel}:{line}", ""])
            continue
        for m in RUST_ROUTE.finditer(src):
            a, _ = args(src, m.end() - 1)
            if len(a) < 2:
                continue
            path = path_of(a[0], known)
            if not path.startswith(("/", "{")):
                continue
            line = src.count("\n", 0, m.start()) + 1
            kind = "nest" if m.group(1).startswith("nest") else "http"
            before = [p for at, p in own if at < m.start()]
            at = MOUNTS.get(rel, before[-1] if before else mount)
            for verb in ["*"] if kind == "nest" else methods(a[1]):
                rows.append([repo, kind, verb, at + path, f"{rel}:{line}", ""])
    return rows


def antares_api(root):
    """`api_routes()` under the API root, and the Admin surface's `PATHS` under `/q`."""
    f = root / "crates/antares-api/src/lib.rs"
    src = f.read_text()
    rel = f.relative_to(root).as_posix()
    rows = []
    body = src[src.index("pub fn api_routes()") :]
    body = body[: body.index("\n}\n")]
    offset = src.index("pub fn api_routes()")
    for m in re.finditer(r'\(\s*"(/[^"]*)",', body):
        a, _ = args(body, m.start())
        line = src.count("\n", 0, offset + m.start()) + 1
        for verb in methods(a[1]):
            rows.append(["antares", "http", verb, "/ngsi-ld/v1" + m.group(1), f"{rel}:{line}", ""])
    paths = re.search(r"const PATHS: \[&'static str; \d+\] = \[(.*?)\];", src, re.S)
    line = src.count("\n", 0, paths.start()) + 1
    for p in re.findall(r'"([^"]+)"', paths.group(1)):
        rows.append(["antares", "http", "*", "/q" + p, f"{rel}:{line}", ""])
    return rows


def tools(repo, root, pattern, files):
    rows = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        src = without_tests(f.read_text())
        for m in re.finditer(pattern, src):
            line = src.count("\n", 0, m.start()) + 1
            rows.append([repo, "mcp-tool", "call", m.group(1), f"{rel}:{line}", ""])
    return rows


def antares_tools(root):
    """The first name of every entry of `TOOLS`, however the entry is built."""
    f = root / "crates/antares-mcp/src/tools.rs"
    src = f.read_text()
    at = src.index("[", re.search(r"const TOOLS: \[ToolDef; \d+\] =", src).end())
    entries, _ = args(src, at)
    rel = f.relative_to(root).as_posix()
    line = src.count("\n", 0, at) + 1
    return [
        ["antares", "mcp-tool", "call", re.search(r'"([a-z_]+)"', e).group(1), f"{rel}:{line}", ""]
        for e in entries
        if e
    ]


def collect(code):
    portal = repo_dir(code, "joinedcontext-portal")
    platform = repo_dir(code, "joinedcontext-platform")
    antares = repo_dir(code, "AntaresBroker", "Antares-NGSI-LD-Context-Broker")
    rows = []

    matrix = portal / "tests/authz_matrix.yaml"
    for n, line in enumerate(matrix.read_text().splitlines(), 1):
        m = re.match(r"\s*- route: (\S+) (\S+)", line)
        if m:
            rows.append(["portal", "http", m.group(1), m.group(2), f"tests/authz_matrix.yaml:{n}", ""])
    rows += tools("portal", portal, r'name:\s*"(jc_[a-z0-9_]+)"', sorted((portal / "src/ops").rglob("*.rs")))

    for crate in sorted((platform / "crates").iterdir()):
        if (crate / "src").is_dir():
            rows += rust_routes("platform", platform, crate / "src", "")
    gateway = platform / "crates/context-gateway/src"
    rows += tools("platform", platform, r'Tool\s*\{\s*name:\s*"([a-z_]+)"', sorted(gateway.rglob("*.rs")))

    rows += antares_api(antares)
    for crate in sorted([*(antares / "crates").iterdir(), *(antares / "addons").iterdir()]):
        src = crate / "src"
        if not src.is_dir():
            continue
        if crate.name == "antares-api":
            rows += [
                r
                for r in rust_routes("antares", antares, src, "")
                if not r[4].startswith("crates/antares-api/src/lib.rs")
                or r[3].startswith("/ex/")
            ]
            continue
        rows += rust_routes("antares", antares, src, mount_of(src))
    rows += antares_tools(antares)

    for row in rows:
        row[5] = authz(row)
    return sorted({tuple(r) for r in rows})


def authz(row):
    repo, _, _, route, source, _ = row
    for r, start, test in COVERAGE:
        if r == repo and (source.startswith(start) or route.startswith(start)):
            return test
    return ""


def read():
    with CSV.open(newline="") as f:
        return [tuple(r[k] for k in FIELDS) for r in csv.DictReader(f)]


def write(rows):
    with CSV.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(rows)


def check(rows, code_rows):
    errors = [f"no authz test or task: {r[0]} {r[2]} {r[3]} ({r[4]})" for r in rows if not r[5]]
    if code_rows is not None:
        key = lambda r: r[:4]  # noqa: E731 - the identity of a row, its line and test aside
        have, served = {key(r) for r in rows}, {key(r) for r in code_rows}
        errors += [f"served, no row: {' '.join(k)}" for k in sorted(served - have)]
        errors += [f"row, not served: {' '.join(k)}" for k in sorted(have - served)]
    return errors


def main(argv):
    code = os.environ.get("JC_CODE_DIR")
    code_rows = collect(pathlib.Path(code)) if code else None
    if "--check" not in argv:
        if code_rows is None:
            raise SystemExit("JC_CODE_DIR names the folder holding the repositories")
        old = {r[:4]: r[5] for r in read()} if CSV.exists() else {}
        write([r[:5] + (r[5] or old.get(r[:4], ""),) for r in code_rows])
        return 0
    errors = check(read(), code_rows)
    for e in errors:
        print(e, file=sys.stderr)
    print(f"{len(read())} routes and tools, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
