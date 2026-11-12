#!/usr/bin/env python3
"""Headless OAuth login of mcp-admin to the jc-prod Portal MCP server, as an MCP client does (T-6807).

Discovery: the 401's WWW-Authenticate names the protected-resource document, which names the
Keycloak realm. The realm offers no dynamic registration, so the script uses the realm's public
client for local MCP programs, `mcp-local` (PKCE S256, loopback callback on any port). Playwright
fills the Keycloak login page; the code never reaches a listener, the browser route catches it.

The login is read from Secret jc-prod/mcp-admin-login (kubectl, read only) and stays in memory.
Tokens live only in $HOME/.jc-mcp-admin/token.json (0600); `logout` deletes it.

  mcp_login.py login                     # browser login, writes the token file
  mcp_login.py tools [out.json]          # initialize + tools/list
  mcp_login.py call <tool> '<json args>' # one tools/call, prints the result JSON
  mcp_login.py anon                      # anonymous POST: expects 401 + WWW-Authenticate
  mcp_login.py logout                    # deletes the token file
"""
import base64, hashlib, json, os, secrets, subprocess, sys, time, urllib.parse
import requests

MCP = "https://portal.smartcity.joinedcontext.com/api/v1/mcp"
CLIENT = "mcp-local"
REDIRECT = "http://127.0.0.1:42239/callback"
SCOPE = "openid mcp:portal"
TOKEN_DIR = os.path.join(os.environ["HOME"], ".jc-mcp-admin")
TOKEN_FILE = os.path.join(TOKEN_DIR, "token.json")
KUBECONFIG = "/workspace/.secrets/kubeconfig-dev.yaml"


def discover():
    r = requests.post(MCP, json={}, timeout=20)
    assert r.status_code == 401, r.status_code
    hdr = r.headers["WWW-Authenticate"]
    meta_url = hdr.split('resource_metadata="')[1].split('"')[0]
    issuer = requests.get(meta_url, timeout=20).json()["authorization_servers"][0]
    return requests.get(issuer + "/.well-known/openid-configuration", timeout=20).json()


def secret_login():
    out = subprocess.run(["kubectl", "-n", "jc-prod", "get", "secret", "mcp-admin-login", "-o", "json"],
                         env={**os.environ, "KUBECONFIG": KUBECONFIG}, capture_output=True, check=True, text=True)
    data = json.loads(out.stdout)["data"]
    return base64.b64decode(data["username"]).decode(), base64.b64decode(data["password"]).decode()


def save(tok):
    tok["obtained_at"] = int(time.time())
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)


def login():
    from playwright.sync_api import sync_playwright
    oidc = discover()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = oidc["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "client_id": CLIENT, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPE,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP})
    user, pw = secret_login()
    got = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()

        # Keycloak answers 302 to the loopback callback; Playwright routes do not see redirected
        # requests, so the request event records it (nothing listens on the port).
        page.on("request", lambda req: req.url.startswith(REDIRECT) and got.setdefault("url", req.url))
        page.goto(url)
        page.fill("#username", user)
        page.fill("#password", pw)
        page.click("#kc-login")
        for _ in range(100):
            if "url" in got:
                break
            page.wait_for_timeout(100)
        if "url" not in got:
            # A consent screen or an error page: say which, never the page's form values.
            sys.exit(f"login did not reach the callback; page title {page.title()!r} at {page.url.split('?')[0]}")
        b.close()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(got["url"]).query)
    if q.get("state", [""])[0] != state:
        sys.exit("state mismatch")
    if "error" in q:
        sys.exit(f"authorization error: {q['error'][0]}")
    r = requests.post(oidc["token_endpoint"], timeout=20, data={
        "grant_type": "authorization_code", "client_id": CLIENT, "code": q["code"][0],
        "redirect_uri": REDIRECT, "code_verifier": verifier})
    r.raise_for_status()
    tok = r.json()
    tok["token_endpoint"] = oidc["token_endpoint"]
    save(tok)
    print(f"login ok: scope={tok.get('scope')!r} expires_in={tok.get('expires_in')}")


def access_token():
    with open(TOKEN_FILE) as f:
        tok = json.load(f)
    if time.time() > tok["obtained_at"] + tok["expires_in"] - 30:
        r = requests.post(tok["token_endpoint"], timeout=20, data={
            "grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": tok["refresh_token"]})
        if r.status_code != 200:
            sys.exit(f"refresh refused ({r.status_code}): run `mcp_login.py login` again")
        new = r.json()
        new["token_endpoint"] = tok["token_endpoint"]
        save(new)
        tok = new
    return tok["access_token"]


class Session:
    def __init__(self):
        self.h = {"Authorization": "Bearer " + access_token(), "Accept": "application/json, text/event-stream",
                  "Content-Type": "application/json"}
        self.n = 0
        self.last = None
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "jc-worker-mcp-check", "version": "1"}})
        self.init = init
        self.notify("notifications/initialized")

    def _post(self, body):
        r = requests.post(MCP, headers=self.h, json=body, timeout=120)
        if "Mcp-Session-Id" in r.headers:
            self.h["Mcp-Session-Id"] = r.headers["Mcp-Session-Id"]
        self.last = r.status_code
        return r

    def notify(self, method):
        self._post({"jsonrpc": "2.0", "method": method})

    def rpc(self, method, params):
        self.n += 1
        r = self._post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            msgs = [json.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")]
            msg = next((m for m in msgs if m.get("id") == self.n), {"error": "no answer in stream"})
        else:
            msg = r.json() if r.content else {"error": f"empty {r.status_code}"}
        return {"http": r.status_code, **msg}

    def call(self, tool, args):
        return self.rpc("tools/call", {"name": tool, "arguments": args})


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
    elif cmd == "tools":
        s = Session()
        res = s.rpc("tools/list", {})
        print(f"initialize {s.init['http']} server={s.init.get('result', {}).get('serverInfo')}")
        tools = res.get("result", {}).get("tools", [])
        print(f"tools/list {res['http']}: {len(tools)} tools")
        if len(sys.argv) > 2:
            with open(sys.argv[2], "w") as f:
                json.dump({"initialize_http": s.init["http"], "serverInfo": s.init.get("result", {}).get("serverInfo"),
                           "tools_list_http": res["http"], "tools": tools}, f, indent=1)
    elif cmd == "call":
        s = Session()
        print(json.dumps(s.call(sys.argv[2], json.loads(sys.argv[3] if len(sys.argv) > 3 else "{}")), indent=1))
    elif cmd == "anon":
        r = requests.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=20)
        print(r.status_code, "WWW-Authenticate:", r.headers.get("WWW-Authenticate"))
    elif cmd == "logout":
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        print("token file removed" if not os.path.exists(TOKEN_FILE) else "token file still there")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
