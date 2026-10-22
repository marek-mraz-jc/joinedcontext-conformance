"""Atom, RSS 2.0 and JSON Feed validator (T-5201).

`fixtures/good.{atom,rss,json}` pass with no finding, and each rule has one broken twin, made from a good
fixture by the one edit in BROKEN, that fails with exactly that rule: a check that stops biting goes red
here. The HTTP rules run against a local http.server; nothing here needs a network.
"""

from __future__ import annotations

import http.server
import pathlib
import sys
import threading

import pytest

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import validate_feed as vf

FIXTURES = HERE / "fixtures"
SELF = "https://feeds.example.org/x/views/podujatia/feed/v1/"
GOOD = {"atom": "atom.xml", "rss": "rss.xml", "json": "feed.json"}
E3 = "tag:feeds.example.org,2026:podujatia/urn:ngsi-ld:Event:3"
E2 = "tag:feeds.example.org,2026:podujatia/urn:ngsi-ld:Event:2"

# rule -> (format of the good fixture, text to replace, replacement)
BROKEN = {
    "xml-parse": ("atom", '<?xml version="1.0" encoding="utf-8"?>', '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE feed [<!ENTITY t "Trh">]>'),
    "atom-schema": ("atom", "<title>Trh</title>", ""),
    "atom-id-unique": ("atom", E2, E3),
    "atom-id-iri": ("atom", E2, "event-2"),
    "atom-date": ("atom", "<updated>2026-10-01T08:00:00Z</updated>", "<updated>2026-10-01T08:00:00</updated>"),
    "atom-author": ("atom", "<author><name>Banskobystrický samosprávny kraj</name><uri>https://www.bbsk.sk/</uri></author>", ""),
    "atom-author-name": ("atom", "Banskobystrický samosprávny kraj", "default"),
    "atom-href": ("atom", 'href="https://feeds.example.org/e/2"', 'href="/e/2"'),
    "atom-self": ("atom", 'href="https://feeds.example.org/x/views/podujatia/feed/v1/atom.xml"', 'href="https://feeds.example.org/other.xml"'),
    "atom-lang": ("atom", 'xml:lang="sk"', 'xml:lang="en"'),
    "atom-order": ("atom", "<updated>2026-10-01T08:00:00Z</updated>", "<updated>2026-10-21T09:00:00Z</updated>"),
    "atom-text": ("atom", "&lt;/b&gt; &amp; hostia", " &amp; hostia"),
    "rss-channel": ("rss", "<description>Podujatia, najnovšie prvé.</description>", ""),
    "rss-item": ("rss", "<description>Výstava obrazov.</description>", ""),
    "rss-guid-unique": ("rss", E2, E3),
    "rss-guid-permalink": ("rss", f'<guid isPermaLink="false">{E2}</guid>', "<guid>event-2</guid>"),
    "rss-date": ("rss", "21 Oct 2026 06:00 GMT", "21 Oct 26 06:00 GMT"),
    "rss-self": ("rss", 'rel="self"', 'rel="related"'),
    "rss-enclosure": ("rss", ' length="1024"', ""),
    "rss-title-html": ("rss", "<title>Trh</title>", "<title>&lt;b&gt;Trh&lt;/b&gt;</title>"),
    "rss-lang": ("rss", "<language>sk</language>", "<language>en-us</language>"),
    "json-parse": ("json", '"items": [', '"items": [,'),
    "json-schema": ("json", '"version": "https://jsonfeed.org/version/1.1"', '"version": "1.1"'),
    "json-id-unique": ("json", f'"id": "{E2}"', f'"id": "{E3}"'),
    "json-content": ("json", '"content_text": "Trh",', ""),
    "json-date": ("json", '"date_modified": "2026-10-22T08:00:00Z"', '"date_modified": "22.10.2026"'),
    "json-self": ("json", '"feed_url": "https://feeds.example.org/x/views/podujatia/feed/v1/feed.json",', ""),
    "json-author-name": ("json", '"name": "Banskobystrický samosprávny kraj"', '"name": "Default"'),
    "json-lang": ("json", '"language": "sk-SK"', '"language": "en"'),
}


def good(fmt: str) -> str:
    return (FIXTURES / f"good.{fmt}").read_text(encoding="utf-8")


def check(fmt: str, text: str) -> list[vf.Finding]:
    return vf.validate(text.encode("utf-8"), fmt, SELF + GOOD[fmt], "sk")


def documented_rules(kind: str) -> set[str]:
    block = vf.__doc__.split(kind, 1)[1].split("\n\n", 2)[1]
    return {line.split()[0] for line in block.splitlines() if line.startswith("    ")}


@pytest.mark.parametrize("fmt", sorted(GOOD))
def test_the_good_feed_has_no_finding(fmt):
    assert check(fmt, good(fmt)) == []


def test_every_document_rule_has_a_broken_fixture():
    assert set(BROKEN) == documented_rules("Document rules:")


@pytest.mark.parametrize("rule", sorted(BROKEN))
def test_a_broken_feed_fails_with_its_named_rule(rule):
    fmt, old, new = BROKEN[rule]
    text = good(fmt)
    assert text.count(old) >= 1, f"the edit for {rule} no longer applies to good.{fmt}"
    findings = check(fmt, text.replace(old, new, 1))
    assert {f.rule for f in findings} == {rule}, [str(f) for f in findings]
    assert all(f.section for f in findings)


def test_the_format_is_sniffed():
    assert [vf.sniff(good(f).encode()) for f in ("atom", "rss", "json")] == ["atom", "rss", "json"]


def test_an_entity_bomb_and_an_external_entity_are_refused():
    findings = vf.validate((FIXTURES / "bomb.atom").read_bytes())
    assert [f.rule for f in findings] == ["xml-parse"]
    assert "root:" not in str(findings)


def test_a_missing_language_is_a_warning_not_an_error():
    findings = check("rss", good("rss").replace("<language>sk</language>", ""))
    assert [(f.level, f.rule) for f in findings] == [("warning", "rss-lang")]


def test_the_three_formats_of_one_dataset_carry_one_item_list():
    feeds = {f: good(f).encode() for f in GOOD}
    assert vf.item_ids(feeds["atom"])[0] == E3
    assert vf.cross_check(feeds) == []
    swapped = good("json").replace(E3, "x").replace(E2, E3).replace("x", E2, 1)
    findings = vf.cross_check({**feeds, "json": swapped.encode()})
    assert {f.rule for f in findings} == {"cross-ids"}


def test_the_cli_exits_1_on_an_error_and_0_on_a_clean_feed(tmp_path, capsys):
    bad = tmp_path / "bad.atom"
    bad.write_text(good("atom").replace('xml:lang="sk"', 'xml:lang="en"'), encoding="utf-8")
    assert vf.main(["--lang", "sk", "--self", SELF + "atom.xml", str(bad)]) == 1
    assert "ERROR atom-lang (RFC 4287 2)" in capsys.readouterr().out
    assert vf.main(["--lang", "sk", "--self", SELF + "atom.xml", str(FIXTURES / "good.atom")]) == 0


def test_a_dataset_page_advertises_its_feeds():
    page = (
        '<link rel="alternate" type="application/atom+xml" href="/f/atom.xml">'
        '<link rel="alternate" type="text/turtle" href="/d.ttl">'
        '<link rel="alternate" type="application/feed+json; charset=utf-8" href="https://x.example/f.json">'
    )
    assert vf.advertised(page, "https://x.example/dataset/a") == [
        ("atom", "https://x.example/f/atom.xml"),
        ("json", "https://x.example/f.json"),
    ]


# --- HTTP rules against a local server -----------------------------------------------------------


class Feeds(http.server.BaseHTTPRequestHandler):
    """Serves good.atom at /good.atom with every header right, and at /bare.atom with none of them."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        body = (
            good("atom")
            .replace(SELF + "atom.xml", base + self.path.split("?")[0])
            .replace("https://feeds.example.org/x/views/podujatia/feed/v1/atom.xml?page=2", base + self.path.split("?")[0] + "?page=2")
            .encode()
        )
        if "page=2" in self.path:
            body = body.replace(b"Event:", b"Event:p2-")
        bare = self.path.startswith("/bare")
        if self.path.startswith("/big"):
            body = body + b" " * 4096
        if not bare and self.headers.get("If-None-Match") == '"v1"':
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        if bare:
            self.send_header("Content-Type", "application/xml")
        else:
            self.send_header("Content-Type", "application/atom+xml; charset=utf-8")
            self.send_header("ETag", '"v1"')
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Feeds)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_a_well_served_feed_passes_every_http_rule(server):
    findings, body = vf.check_http(server + "/good.atom", lang="sk")
    assert findings == [] and body


def test_a_bare_server_fails_content_type_validator_and_cors(server):
    findings, _ = vf.check_http(server + "/bare.atom", lang="sk")
    assert {f.rule for f in findings} == {"http-content-type", "http-validator", "http-cors"}


def test_a_conditional_get_must_answer_304(server, monkeypatch):
    real = vf.fetch
    monkeypatch.setattr(vf, "fetch", lambda url, headers=None, *a, **k: real(url, {k2: v for k2, v in (headers or {}).items() if k2 != "If-None-Match"}, *a, **k))
    findings, _ = vf.check_http(server + "/good.atom", lang="sk")
    assert {f.rule for f in findings} == {"http-304"}


def test_a_next_page_that_repeats_this_one_fails(server, monkeypatch):
    real = vf.fetch
    monkeypatch.setattr(vf, "fetch", lambda url, *a, **k: real(url.replace("?page=2", ""), *a, **k))
    findings, _ = vf.check_http(server + "/good.atom", lang="sk")
    assert {f.rule for f in findings} == {"http-paging-advance"}


def test_a_feed_over_the_size_cap_is_not_read(server):
    findings, body = vf.check_http(server + "/big.atom", max_bytes=1024)
    assert [f.rule for f in findings] == ["http-size"] and body == b""


def test_a_url_outside_the_allow_list_is_never_fetched(server):
    findings, _ = vf.check_http(server + "/good.atom", allowed=lambda u: u.startswith("https://"))
    assert [f.rule for f in findings] == ["http-status"] and "allow-list" in findings[0].message


def test_the_sweep_counts_only_advertised_feeds_and_fails_one_that_answers_404(tmp_path, monkeypatch):
    """Owner 2026-10-22T09:25Z: feeds are off by default; a page that advertises one must serve it."""
    base = "https://cat.example"
    search = b'{"result": {"results": [{"name": "podujatia"}, {"name": "ovzdusie"}]}}'
    pages = {
        f"{base}/api/3/action/package_search?rows=100&start=0&fl=name": vf.Answer(200, {}, search),
        f"{base}/": vf.Answer(200, {}, b"<html></html>"),
        f"{base}/dataset/podujatia": vf.Answer(200, {}, b'<link rel="alternate" type="application/atom+xml" href="/f/atom.xml">'),
        f"{base}/dataset/ovzdusie": vf.Answer(200, {}, b'<link rel="alternate" type="text/turtle" href="/d.ttl">'),
        f"{base}/f/atom.xml": vf.Answer(404, {}, b""),
    }
    fetched = []

    def fake(url, headers=None, allowed=None, max_bytes=vf.MAX_BYTES):
        assert allowed(url), url
        fetched.append(url)
        return pages.get(url, vf.Answer(200, {}, b'{"result": {"results": []}}'))

    monkeypatch.setattr(vf, "fetch", fake)
    out = tmp_path / "sweep.tsv"
    assert vf.sweep(base, str(out), "sk", []) == 1
    rows = [line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[1:] == [["podujatia", "atom", f"{base}/f/atom.xml", "1", "0", "http-status"]]
    assert f"{base}/d.ttl" not in fetched


def test_the_sweep_refuses_a_plain_http_catalogue(tmp_path):
    with pytest.raises(SystemExit):
        vf.sweep("http://cat.example", str(tmp_path / "x.tsv"), None, [])
