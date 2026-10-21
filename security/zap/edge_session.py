"""Sign a person in to the Portal the way a browser does and print the edge session cookie (T-5107).

The authenticated ZAP baseline needs a session, and the Portal has no token login: the edge runs
the OIDC code flow and keeps the session in its `__Host-jc_edge` cookie. This walks that flow with
the standard library (Portal -> Keycloak login form -> callback -> Portal) and prints
the `__Host-jc_edge*` cookies on stdout, the value ZAP sends as its Cookie header.

    PORTAL_VIEWER_USER=demo.viewer@hel.fi PORTAL_VIEWER_PASSWORD=... \
        python3 security/zap/edge_session.py https://portal.dev.joinedcontext.com/

The password comes from the environment only and is never printed; a failed sign-in names the
step that failed and the HTTP status, never the form it posted.
"""

import http.cookiejar
import os
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

COOKIE = "__Host-jc_edge"
TIMEOUT = 20


class LoginForm(HTMLParser):
    """The action of Keycloak's `kc-form-login` form, or None when the page has none."""

    def __init__(self):
        super().__init__()
        self.action = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "kc-form-login":
            self.action = a.get("action")


def form_action(html: str) -> str | None:
    p = LoginForm()
    p.feed(html)
    return p.action


def edge_cookie(portal: str, user: str, password: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(portal, timeout=TIMEOUT) as r:
        page, login_url = r.read().decode("utf-8", "replace"), r.geturl()
    action = form_action(page)
    if not action:
        raise SystemExit(f"no Keycloak login form at {urllib.parse.urlsplit(login_url).netloc}: is the Portal redirecting to the realm?")
    action = urllib.parse.urljoin(login_url, action)
    if urllib.parse.urlsplit(action).scheme != "https":
        raise SystemExit("the login form posts over plain http: refusing to send the password")
    body = urllib.parse.urlencode({"username": user, "password": password, "credentialId": ""}).encode()
    with opener.open(urllib.request.Request(action, data=body), timeout=TIMEOUT) as r:
        landed = r.geturl()
    # The edge sets its cookie on the first redirect already (it holds the OIDC state), so only a
    # return to the Portal's own host proves the sign-in; a wrong password stays on the realm.
    if urllib.parse.urlsplit(landed).netloc != urllib.parse.urlsplit(portal).netloc:
        raise SystemExit(f"sign-in did not return to the Portal (landed on {urllib.parse.urlsplit(landed).netloc}): wrong password or the account needs an action")
    # A long session is split over several cookies (`__Host-jc_edge`, `__Host-jc_edge2`, ...).
    edge = [f"{c.name}={c.value}" for c in jar if c.name.startswith(COOKIE)]
    if edge:
        return "; ".join(edge)
    raise SystemExit(f"the Portal set no {COOKIE} cookie after the sign-in")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: edge_session.py <portal url>")
    user = os.environ.get("PORTAL_VIEWER_USER", "demo.viewer@hel.fi")
    password = os.environ.get("PORTAL_VIEWER_PASSWORD")
    if not password:
        raise SystemExit("PORTAL_VIEWER_PASSWORD is not set (the demo viewer's password from the secrets mount)")
    print(edge_cookie(sys.argv[1], user, password))


if __name__ == "__main__":
    main()
