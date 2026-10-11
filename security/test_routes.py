"""The inventory's reader against a tiny tree of the three repositories (T-3832)."""

import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location("routes", pathlib.Path(__file__).with_name("routes.py"))
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)

PORTAL_MATRIX = """routes:
  - route: GET /api/v1/projects
    rule: "read"
"""
PORTAL_OPS = 'Operation { name: "jc_project_list", .. }\n'
PLATFORM = '''use axum::Router;
const HOOK: &str = "/v1/hook";
pub fn router() -> Router {
    // .route("/commented", get(gone))
    Router::new()
        .route("/healthz", get(|| async { "ok" }))
        .route(HOOK, post(hook).delete(unhook))
        .route("/v1/{*rest}", axum::routing::any(proxy))
}
#[cfg(test)]
mod tests {
    fn t() { Router::new().route("/notify", post(x)); }
}
'''
HYPER = '''use hyper::body::Incoming;
pub async fn handle(path: &str) {
    if path == "/jobs" { return; }
    let rest = path.strip_prefix("/apps/");
}
pub fn admin(path: &str) {
    match path {
        "/metrics" => (),
        _ => (),
    }
}
'''
# A match on strings outside a hyper server is no route.
NOT_A_SERVER = '''fn kind(s: &str) { match s { "/" => (), _ => () } }
'''
ANTARES_API = '''pub fn api_routes() -> Vec<(&'static str, MethodRouter<AppState>)> {
    vec![
        ("/entities", post(create).get(query)),
    ]
}

impl Admin {
    const PATHS: [&'static str; 1] = [
        "/health",
    ];
}
'''
ANTARES_WS = '''use axum::Router;
impl Surface for Root {
    fn prefix(&self) -> &'static str {
        "/x/ws"
    }
    fn router(&self) -> Router { Router::new().route("/ngsi-ld/v1", get(root)) }
}
impl Surface for Views {
    fn prefix(&self) -> &'static str {
        VIEW_ROOT
    }
    fn router(&self) -> Router { Router::new().route("/{view}/ws", get(view)) }
}
const VIEW_ROOT: &str = "/x/views";
'''
ANTARES_TOOLS = '''pub(crate) const TOOLS: [ToolDef; 2] = [
    write("append_attributes", OperationId::AppendAttributes, "d", APPEND, "result"),
    ToolDef {
        name: "batch_query",
        op: OperationId::QueryEntitiesPost,
    },
];
'''


@pytest.fixture
def code(tmp_path):
    files = {
        "joinedcontext-portal/tests/authz_matrix.yaml": PORTAL_MATRIX,
        "joinedcontext-portal/src/ops/mod.rs": PORTAL_OPS,
        "joinedcontext-platform/crates/proxy/src/lib.rs": PLATFORM,
        "joinedcontext-platform/crates/context-gateway/src/mcp.rs": 'const TOOLS: &[Tool] = &[Tool { name: "query_entities", .. }];\n',
        "joinedcontext-platform/crates/host/src/server.rs": HYPER,
        "joinedcontext-platform/crates/host/src/mapping.rs": NOT_A_SERVER,
        "AntaresBroker/crates/antares-api/src/lib.rs": ANTARES_API,
        "AntaresBroker/crates/antares-ws/src/lib.rs": ANTARES_WS,
        "AntaresBroker/crates/antares-mcp/src/tools.rs": ANTARES_TOOLS,
        "AntaresBroker/addons/.keep": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return tmp_path


def served(code):
    return {r[:4] for r in routes.collect(code)}


def test_every_route_and_tool_of_the_three_repositories_is_read(code):
    assert served(code) == {
        ("portal", "http", "GET", "/api/v1/projects"),
        ("portal", "mcp-tool", "call", "jc_project_list"),
        ("platform", "http", "GET", "/healthz"),
        ("platform", "http", "POST", "/v1/hook"),
        ("platform", "http", "DELETE", "/v1/hook"),
        ("platform", "http", "ANY", "/v1/{*rest}"),
        ("platform", "http", "*", "/jobs"),
        ("platform", "http", "*", "/metrics"),
        ("platform", "http", "*", "/apps/{*rest}"),
        ("platform", "mcp-tool", "call", "query_entities"),
        ("antares", "http", "POST", "/ngsi-ld/v1/entities"),
        ("antares", "http", "GET", "/ngsi-ld/v1/entities"),
        ("antares", "http", "*", "/q/health"),
        ("antares", "http", "GET", "/x/ws/ngsi-ld/v1"),
        ("antares", "http", "GET", "/x/views/{view}/ws"),
        ("antares", "mcp-tool", "call", "append_attributes"),
        ("antares", "mcp-tool", "call", "batch_query"),
    }


def test_a_route_without_a_test_or_a_task_fails():
    row = ("platform", "http", "GET", "/v1/new", "crates/x/src/lib.rs:1", "")
    assert routes.check([row], None) == [
        "no authz test or task: platform GET /v1/new (crates/x/src/lib.rs:1)"
    ]


def test_a_served_route_without_a_row_and_a_row_nobody_serves_fail(code):
    rows = [r for r in routes.collect(code) if r[3] != "/jobs"]
    rows.append(("platform", "http", "GET", "/gone", "crates/x/src/lib.rs:1", "task:T-1"))
    errors = routes.check(rows, routes.collect(code))
    assert "served, no row: platform http * /jobs" in errors
    assert "row, not served: platform http GET /gone" in errors


def test_the_first_coverage_rule_that_matches_decides():
    row = ["antares", "http", "GET", "/q/views", "crates/antares-ops/src/views.rs:1", ""]
    assert routes.authz(row) == "task:T-3855"
    row[3] = "/q/tenants"
    assert routes.authz(row) == "antares crates/antares-api/tests/admin_auth.rs"
