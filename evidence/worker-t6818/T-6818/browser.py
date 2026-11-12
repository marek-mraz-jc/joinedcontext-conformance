#!/usr/bin/env python3
"""Record one browser segment of the T-6818 video as t6818-approver, signed in off camera.
  browser.py <segment> <change id or -> [phone]
Segments: before (public page), approve (approvals list -> change -> diff -> Approve), after (Portal
Endpoint page + public page). Video goes to vid/<segment>[-phone].webm. Prints each step with its time
and the HTTP code of the approve call; never a cookie or token.
"""
import os, sys, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approver_login import kc_steps, creds

PORTAL = "https://portal.smartcity.joinedcontext.com"
PUBLIC = "https://api.smartcity.joinedcontext.com/api/endpoint/6wrymzu5x2uwjo2xqei66ppw53mafvbu/"
seg, change = sys.argv[1], sys.argv[2]
phone = len(sys.argv) > 3
STATE = os.path.join(os.environ["SECRET_DIR"], "state.json")
view, size = ({"width": 400, "height": 860}, {"width": 400, "height": 860}) if phone else \
             ({"width": 768, "height": 864}, {"width": 768, "height": 864})  # upscaled to 960x1080 in compose.py
t0 = time.time()
def step(msg): print(f"{time.time() - t0:6.1f}s {msg}", flush=True)

def mark(pg, loc):
    """Rings the element about to be used, so the viewer sees where the click goes."""
    loc.scroll_into_view_if_needed()
    loc.evaluate("e => { e.style.outline = '3px solid #e64553'; e.style.outlineOffset = '3px'; }")
    pg.wait_for_timeout(900)

def go(pg, url, label):
    pg.goto(url, wait_until="domcontentloaded"); pg.wait_for_timeout(2500); step(f"open {label}: {url.split('?')[0]} | {pg.title()}")

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport=view, storage_state=STATE, record_video_dir="vid", record_video_size=size, locale="en-GB")
    pg = ctx.new_page()
    if seg == "before":
        go(pg, PUBLIC, "public Endpoint page"); pg.wait_for_timeout(3000)
    elif seg == "approve":
        go(pg, f"{PORTAL}/projects/helsinki/approvals?lang=en", "Approvals list")
        row = pg.get_by_role("link", name=change).first if pg.get_by_role("link", name=change).count() else pg.get_by_text(change).first
        mark(pg, row); row.click(); pg.wait_for_timeout(2500); step(f"open change {change}: {pg.title()}")
        diff = pg.get_by_text("Title › En").first
        mark(pg, diff); pg.wait_for_timeout(2500); step("read the diff: Title › En before/after")
        box = pg.get_by_placeholder("helsinki-weather")
        mark(pg, box); box.press_sequentially("helsinki-weather", delay=70); step("typed helsinki-weather to confirm a red-lane change")
        btn = pg.get_by_role("button", name="Approve", exact=True)
        mark(pg, btn)
        with pg.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/approve")) as resp:
            btn.click()
        step(f"Approve clicked: POST .../changes/{change}/approve -> {resp.value.status}")
        if "idm." in pg.url:
            pg.add_style_tag(content="#password,#otp{filter:blur(8px)}")
            user, pw = creds()
            while "idm." in pg.url and kc_steps(pg, user, pw):
                pg.wait_for_timeout(2000)
            step("step-up sign-in answered (fields blurred)")
        pg.wait_for_timeout(4000); pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(3000)
        step("change page after approve: " + " | ".join(t for t in pg.locator("main").inner_text().split("\n")[:12] if t.strip())[:300])
    elif seg == "after":
        go(pg, f"{PORTAL}/projects/helsinki/endpoints/helsinki-weather?lang=en", "Portal Endpoint page")
        h = pg.locator("h1").first; mark(pg, h); step("Portal heading: " + h.inner_text()); pg.wait_for_timeout(1500)
        go(pg, PUBLIC, "public Endpoint page")
        h = pg.locator("h1").first; mark(pg, h); step("public heading: " + h.inner_text()); pg.wait_for_timeout(2000)
    vid = pg.video
    ctx.close(); b.close()
    dst = os.path.join("vid", seg + ("-" + change if change != "-" else "") + ("-phone" if phone else "") + ".webm")
    os.replace(vid.path(), dst); step("video " + dst)
