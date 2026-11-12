#!/usr/bin/env python3
"""Sign t6818-approver in to the jc-prod Portal off camera and save the browser state (T-6818).
The login comes from Secret jc-prod/t6818-approver-login and stays in memory; a TOTP the realm makes
it enrol is kept only in $SECRET_DIR/totp (0600), deleted with the person after the recording.
  approver_login.py <state.json>
"""
import base64, hashlib, hmac, json, os, re, struct, subprocess, sys, time
from playwright.sync_api import sync_playwright

SECRET_DIR = os.environ["SECRET_DIR"]
PORTAL = "https://portal.smartcity.joinedcontext.com"


def totp(secret):
    # The realm's OTP policy is HMAC-SHA256 (deployment scripts/keycloak-login-journey.py).
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha256).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def creds():
    d = json.loads(subprocess.run(["kubectl", "-n", "jc-prod", "get", "secret", "t6818-approver-login", "-o", "json"],
        env={**os.environ, "KUBECONFIG": "/workspace/.secrets/kubeconfig-dev.yaml"},
        capture_output=True, check=True, text=True).stdout)["data"]
    return base64.b64decode(d["username"]).decode(), base64.b64decode(d["password"]).decode()


def kc_steps(pg, user, pw):
    """Answers whatever Keycloak page is open: login, OTP enrolment or OTP prompt. Returns False when none."""
    tf = os.path.join(SECRET_DIR, "totp")
    if pg.locator("#password").count() and pg.locator("#username").count():
        pg.fill("#username", user); pg.fill("#password", pw); pg.click("#kc-login"); return True
    if pg.locator("#password").count():
        pg.fill("#password", pw); pg.click("#kc-login"); return True
    if pg.locator("#kc-totp-secret-key").count() or pg.locator("#mode-manual").count() or pg.locator("#saveTOTPBtn").count():
        if pg.locator("#mode-manual").count():
            pg.click("#mode-manual"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(500)
        secret = re.sub(r"\s", "", pg.locator("#kc-totp-secret-key").inner_text())
        fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, secret.encode()); os.close(fd)
        pg.fill("#totp" if pg.locator("#totp").count() else "#otp", totp(secret))
        if pg.locator("#userLabel").count():
            pg.fill("#userLabel", "t6818")
        pg.click("#saveTOTPBtn, input[type=submit]"); return True
    if pg.locator("#otp").count() and os.path.exists(tf):
        pg.fill("#otp", totp(open(tf).read().strip())); pg.click("#kc-login"); return True
    return False


if __name__ == "__main__":
    user, pw = creds()
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(viewport={"width": 960, "height": 1080}); pg = ctx.new_page()
        pg.goto(PORTAL + "/login", wait_until="networkidle")
        pg.get_by_role("button", name="Sign in").click()
        for _ in range(6):
            pg.wait_for_load_state("networkidle")
            if "idm." not in pg.url or not kc_steps(pg, user, pw):
                break
        # Approving needs a second factor in the session (403 second-factor-required): step up here.
        pg.goto(PORTAL + "/api/v1/auth/login?step_up=true", wait_until="networkidle")
        for _ in range(6):
            pg.wait_for_load_state("networkidle")
            err = pg.locator(".kc-feedback-text, [id^=input-error]").all_inner_texts()
            print("step-up page:", pg.title(), "|", [e for e in err if e.strip()][:2])
            if "idm." not in pg.url or not kc_steps(pg, user, pw):
                break
            pg.wait_for_timeout(1000)
        if "idm." in pg.url:
            info = pg.locator("#kc-totp-settings").inner_text() if pg.locator("#kc-totp-settings").count() else ""
            err = pg.locator("#input-error-otp").inner_text() if pg.locator("#input-error-otp").count() else ""
            print("settings:", re.sub(r"[A-Z2-7]{4}( [A-Z2-7]{4})+", "<key>", info).replace(chr(10), " / ")[:300], "| err:", err)
            sys.exit("step-up stuck at " + pg.url.split("?")[0] + " | " + pg.title() + " | ids: "
                     + ",".join(pg.eval_on_selector_all("[id]", "els=>els.map(e=>e.id)"))[:600])
        pg.wait_for_load_state("networkidle")
        print("at", pg.url.split("?")[0], "|", pg.title())
        ctx.storage_state(path=sys.argv[1]); os.chmod(sys.argv[1], 0o600)
        b.close()
