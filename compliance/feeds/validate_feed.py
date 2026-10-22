#!/usr/bin/env python3
"""Atom (RFC 4287), RSS 2.0 and JSON Feed 1.1 checks of a feed, its HTTP answer and its siblings (T-5201).

    validate_feed.py URL|FILE [--format atom|rss|json] [--lang sk] [--self URL]
    validate_feed.py --cross ATOM RSS JSON          the three feeds of one dataset carry one item list
    validate_feed.py --sweep CKAN_BASE [--out results.tsv] [--lang sk] [--allow-host HOST ...]

Every finding names its rule and the spec section, so a test can ask for exactly the one a broken feed
breaks. Document rules:

    xml-parse             the document is no well-formed XML, or holds a DTD (XML 1.0 2.8; no DTD, no entities)
    atom-schema           RFC 4287 Appendix B RELAX NG
    atom-id-unique        two entries with one atom:id (4.2.6)
    atom-id-iri           an atom:id that is no absolute IRI (4.2.6, RFC 3987)
    atom-date             updated or published not in RFC 3339 with an offset (3.3)
    atom-author           no author on the feed and none on some entry (4.1.1, 4.1.2)
    atom-author-name      a placeholder author name such as "default" (3.2.1)
    atom-href             a relative link href, icon, logo or content src with no xml:base (4.2.7.1, RFC 3986 5.1)
    atom-self             no link rel="self", or one that is not the fetched URL (4.2.7.2, RFC 5005 1)
    atom-lang             xml:lang other than --lang (2, xml:lang)
    atom-order            entries not newest first by updated (profile, T-5201)
    atom-text             an html text construct whose markup is not balanced (3.1.1.2)
    rss-channel           no channel, or a channel without title, link or description (RSS 2.0, Required channel elements)
    rss-item              an item with neither title nor description (RSS 2.0, Elements of item)
    rss-guid-unique       two items with one guid (RSS 2.0, guid)
    rss-guid-permalink    a guid with isPermaLink true that is no absolute http(s) URL (RSS 2.0, guid)
    rss-date              pubDate or lastBuildDate not in RFC 822 with a 4-digit year (RSS 2.0, RFC 822 5)
    rss-self              no atom:link rel="self", or one that is not the fetched URL (RSS Best Practices, atom:link)
    rss-enclosure         an enclosure without url, length or type (RSS 2.0, enclosure)
    rss-title-html        HTML markup in a title (RSS Best Practices, title)
    rss-lang              channel language other than --lang (RSS 2.0, language)
    json-parse            no JSON object (RFC 8259)
    json-schema           the JSON Feed 1.1 schema (JSON Feed 1.1, Top-level and Items)
    json-id-unique        two items with one id (JSON Feed 1.1, Items: id)
    json-content          an item with neither content_html nor content_text (JSON Feed 1.1, Items)
    json-date             date_published or date_modified not in RFC 3339 (JSON Feed 1.1, Items)
    json-self             feed_url missing or not the fetched URL (JSON Feed 1.1, Top-level: feed_url)
    json-author-name      a placeholder author name such as "default" (JSON Feed 1.1, Authors)
    json-lang             language other than --lang (JSON Feed 1.1, Top-level: language)

HTTP rules (a URL only):

    http-status           the feed does not answer 200 (RFC 9110 15.3.1)
    http-size             the body is larger than --max-bytes (profile, T-5201)
    http-content-type     not the format's media type with a charset (RFC 4287 7, RSS Best Practices, JSON Feed 1.1)
    http-validator        neither ETag nor Last-Modified (RFC 9110 8.8)
    http-304              a conditional GET with the validator does not answer 304 (RFC 9110 13.1, 15.4.5)
    http-cors             no Access-Control-Allow-Origin for a foreign Origin (Fetch, CORS protocol)
    http-paging           a next, prev, first or last link that is relative or does not answer 200 (RFC 5005 3)
    http-paging-advance   the next page repeats the entries of this one (RFC 5005 3)

Cross-format rule: `cross-ids`, the Atom, RSS and JSON Feed of one dataset list other item ids or another order.

Prints one line per feed and one per finding (`ERROR rule (section): message`); exits 1 on any error.
Parsing refuses DTDs and never resolves an entity or fetches over the network (no XXE, no billion laughs).
Uses lxml and jsonschema from the CI venv and the standard library only.
"""

from __future__ import annotations

import argparse
import csv
import email.utils
import html.parser
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import jsonschema
from lxml import etree

HERE = pathlib.Path(__file__).parent
ATOM = "http://www.w3.org/2005/Atom"
XML_NS = "http://www.w3.org/XML/1998/namespace"
A = f"{{{ATOM}}}"
UA = {"User-Agent": "joinedcontext-conformance feeds (T-5201)"}
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
MEDIA = {"atom": "application/atom+xml", "rss": "application/rss+xml", "json": "application/feed+json"}
PLACEHOLDER_AUTHORS = {"", "default", "admin", "administrator", "anonymous", "none", "null", "n/a", "root", "unknown", "user"}
PAGING = ("next", "prev", "previous", "first", "last")
RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})")
RFC822 = re.compile(
    r"((Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} "
    r"\d{2}:\d{2}(:\d{2})? (UT|GMT|EST|EDT|CST|CDT|MST|MDT|PST|PDT|[A-IK-Z]|[+-]\d{4})"
)
HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    rule: str
    section: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper()} {self.rule} ({self.section}): {self.message}"


def err(rule: str, section: str, message: str) -> Finding:
    return Finding("error", rule, section, message)


def warn(rule: str, section: str, message: str) -> Finding:
    return Finding("warning", rule, section, message)


# --- shared helpers ------------------------------------------------------------------------------


def is_rfc3339(value: str) -> bool:
    if not RFC3339.fullmatch(value.strip()):
        return False
    try:
        datetime.fromisoformat(value.strip().replace("z", "Z").replace("t", "T"))
    except ValueError:
        return False
    return True


def rfc3339(value: str) -> datetime | None:
    return datetime.fromisoformat(value.strip().replace("z", "Z").replace("t", "T")) if is_rfc3339(value) else None


def is_rfc822(value: str) -> bool:
    if not RFC822.fullmatch(value.strip()):
        return False
    try:
        email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return False
    return True


def is_absolute(url: str) -> bool:
    parts = urllib.parse.urlsplit(url.strip())
    return bool(parts.scheme) and (bool(parts.netloc) or parts.scheme in {"urn", "tag", "mailto"})


def is_http_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url.strip())
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def same_language(tag: str, wanted: str) -> bool:
    return tag.strip().split("-")[0].lower() == wanted.strip().split("-")[0].lower()


class _Balance(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problem = ""

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.problem = self.problem or f"</{tag}> closes {('<' + self.stack[-1] + '>') if self.stack else 'nothing'}"
        else:
            self.stack.pop()


def html_problem(text: str) -> str:
    """Why an escaped HTML fragment is not balanced, or "" when it is."""
    parser = _Balance()
    parser.feed(text)
    parser.close()
    if parser.problem:
        return parser.problem
    return f"<{parser.stack[-1]}> is never closed" if parser.stack else ""


def sniff(data: bytes) -> str:
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"{"):
        return "json"
    match = re.search(rb"<(?:[A-Za-z_][\w.-]*:)?(feed|rss|RDF)\b", head[:4096])
    if match and match.group(1) == b"feed":
        return "atom"
    return "rss"


def parse_xml(data: bytes) -> tuple[etree._Element | None, list[Finding]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, dtd_validation=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        return None, [err("xml-parse", "XML 1.0 2.8", f"not well-formed: {e}")]
    if root.getroottree().docinfo.doctype or root.getroottree().docinfo.internalDTD is not None:
        return None, [err("xml-parse", "XML 1.0 2.8", "the document carries a DTD; a feed needs none and entities are refused")]
    return root, []


# --- Atom ----------------------------------------------------------------------------------------

_ATOM_RNG: etree.RelaxNG | None = None


def atom_rng() -> etree.RelaxNG:
    global _ATOM_RNG
    if _ATOM_RNG is None:
        _ATOM_RNG = etree.RelaxNG(etree.parse(str(HERE / "schema" / "atom.rng")))
    return _ATOM_RNG


def base_of(el: etree._Element) -> str:
    """The absolute xml:base in scope of `el`, or "" when none is."""
    bases = []
    node = el
    while node is not None:
        b = node.get(f"{{{XML_NS}}}base")
        if b is not None:
            bases.append(b)
        node = node.getparent()
    base = ""
    for b in reversed(bases):
        base = urllib.parse.urljoin(base, b) if base else b
    return base if is_absolute(base) else ""


def atom_links(root: etree._Element) -> list[etree._Element]:
    return root.findall(f"{A}link")


def rel_of(link: etree._Element) -> str:
    return link.get("rel", "alternate")


def check_atom(root: etree._Element, url: str | None, lang: str | None) -> list[Finding]:
    out: list[Finding] = []
    if root.tag != f"{A}feed":
        return [err("atom-schema", "RFC 4287 4.1.1", f"the root is {root.tag}, not atom:feed")]
    rng = atom_rng()
    if not rng.validate(root.getroottree()):
        for e in list(rng.error_log)[:5]:
            out.append(err("atom-schema", "RFC 4287 Appendix B", f"line {e.line}: {e.message}"))
    entries = root.findall(f"{A}entry")

    seen: set[str] = set()
    for entry in entries:
        ident = (entry.findtext(f"{A}id") or "").strip()
        if ident in seen:
            out.append(err("atom-id-unique", "RFC 4287 4.2.6", f"atom:id {ident!r} is on two entries"))
        seen.add(ident)
        if ident and not is_absolute(ident):
            out.append(err("atom-id-iri", "RFC 4287 4.2.6", f"atom:id {ident!r} is no absolute IRI"))
    feed_id = (root.findtext(f"{A}id") or "").strip()
    if feed_id and not is_absolute(feed_id):
        out.append(err("atom-id-iri", "RFC 4287 4.2.6", f"the feed's atom:id {feed_id!r} is no absolute IRI"))

    for el in root.iter(f"{A}updated", f"{A}published"):
        if not is_rfc3339(el.text or ""):
            out.append(err("atom-date", "RFC 4287 3.3", f"{etree.QName(el).localname} {el.text!r} is not RFC 3339 with an offset"))

    if not root.findall(f"{A}author"):
        missing = [(e.findtext(f"{A}id") or "?") for e in entries if not e.findall(f"{A}author")]
        if missing:
            out.append(err("atom-author", "RFC 4287 4.1.1", f"no feed author and none on {len(missing)} entries, first {missing[0]!r}"))
    for name in root.iter(f"{A}name"):
        if (name.text or "").strip().lower() in PLACEHOLDER_AUTHORS:
            out.append(err("atom-author-name", "RFC 4287 3.2.1", f"author name {name.text!r} names nobody; give the publisher"))

    for el in root.iter(f"{A}link", f"{A}icon", f"{A}logo", f"{A}content"):
        ref = el.get("href") if el.tag == f"{A}link" else (el.get("src") if el.tag == f"{A}content" else el.text)
        if ref is None or is_absolute(ref) or base_of(el):
            continue
        out.append(err("atom-href", "RFC 4287 4.2.7.1", f"{etree.QName(el).localname} {ref!r} is relative and no xml:base is in scope"))

    selves = [l for l in atom_links(root) if rel_of(l) == "self"]
    if not selves:
        out.append(err("atom-self", "RFC 4287 4.2.7.2", 'the feed has no link rel="self"'))
    elif url:
        href = urllib.parse.urljoin(base_of(selves[0]) or url, selves[0].get("href", ""))
        if href != url:
            out.append(err("atom-self", "RFC 5005 1", f'link rel="self" is {href!r}, the feed was fetched from {url!r}'))

    if lang:
        got = root.get(f"{{{XML_NS}}}lang")
        if got is None:
            out.append(warn("atom-lang", "RFC 4287 2", f"the feed has no xml:lang; expected {lang!r}"))
        elif not same_language(got, lang):
            out.append(err("atom-lang", "RFC 4287 2", f"xml:lang is {got!r}, the content is {lang!r}"))

    stamps = [rfc3339(e.findtext(f"{A}updated") or "") for e in entries]
    for i in range(1, len(stamps)):
        a, b = stamps[i - 1], stamps[i]
        if a and b and b > a:
            out.append(err("atom-order", "profile T-5201", f"entry {i + 1} is newer than entry {i}; list the newest first"))
            break

    for el in root.iter(f"{A}title", f"{A}subtitle", f"{A}summary", f"{A}rights", f"{A}content"):
        if el.get("type") == "html":
            problem = html_problem(el.text or "")
            if problem:
                out.append(err("atom-text", "RFC 4287 3.1.1.2", f"{etree.QName(el).localname} html: {problem}"))
    return out


def atom_ids(root: etree._Element) -> list[str]:
    return [(e.findtext(f"{A}id") or "").strip() for e in root.findall(f"{A}entry")]


# --- RSS 2.0 -------------------------------------------------------------------------------------


def check_rss(root: etree._Element, url: str | None, lang: str | None) -> list[Finding]:
    out: list[Finding] = []
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        return [err("rss-channel", "RSS 2.0, Required channel elements", "no <rss version=\"2.0\"><channel>")]
    if root.get("version") != "2.0":
        out.append(err("rss-channel", "RSS 2.0", f"rss version is {root.get('version')!r}, not \"2.0\""))
    for name in ("title", "link", "description"):
        if channel.find(name) is None:
            out.append(err("rss-channel", "RSS 2.0, Required channel elements", f"the channel has no <{name}>"))

    items = channel.findall("item")
    seen: set[str] = set()
    for n, item in enumerate(items, 1):
        if item.find("title") is None and item.find("description") is None:
            out.append(err("rss-item", "RSS 2.0, Elements of item", f"item {n} has neither title nor description"))
        guid = item.find("guid")
        if guid is not None:
            value = (guid.text or "").strip()
            if value in seen:
                out.append(err("rss-guid-unique", "RSS 2.0, guid", f"guid {value!r} is on two items"))
            seen.add(value)
            if guid.get("isPermaLink", "true") == "true" and not is_http_url(value):
                out.append(err("rss-guid-permalink", "RSS 2.0, guid", f"guid {value!r} is a permalink but no absolute URL; set isPermaLink=\"false\" or give the URL"))
        for enc in item.findall("enclosure"):
            missing = [a for a in ("url", "length", "type") if not enc.get(a)]
            if missing:
                out.append(err("rss-enclosure", "RSS 2.0, enclosure", f"item {n} enclosure lacks {', '.join(missing)}"))

    for el in [*channel.findall("pubDate"), *channel.findall("lastBuildDate"), *channel.findall("item/pubDate")]:
        if not is_rfc822(el.text or ""):
            out.append(err("rss-date", "RSS 2.0, RFC 822 5", f"{el.tag} {el.text!r} is not RFC 822 with a 4-digit year"))

    selves = [l for l in channel.findall(f"{A}link") if l.get("rel") == "self"]
    if not selves:
        out.append(err("rss-self", "RSS Best Practices, atom:link", 'the channel has no atom:link rel="self"'))
    elif url and selves[0].get("href") != url:
        out.append(err("rss-self", "RSS Best Practices, atom:link", f"atom:link rel=\"self\" is {selves[0].get('href')!r}, the feed was fetched from {url!r}"))

    for title in [channel.find("title"), *channel.findall("item/title")]:
        if title is None:
            continue
        if len(title) or HTML_TAG.search(title.text or ""):
            out.append(err("rss-title-html", "RSS Best Practices, title", f"title {''.join(title.itertext())!r} carries HTML; titles are plain text"))

    if lang:
        got = channel.findtext("language")
        if got is None:
            out.append(warn("rss-lang", "RSS 2.0, language", f"the channel has no <language>; expected {lang!r}"))
        elif not same_language(got, lang):
            out.append(err("rss-lang", "RSS 2.0, language", f"language is {got!r}, the content is {lang!r}"))
    return out


def rss_ids(root: etree._Element) -> list[str]:
    channel = root.find("channel")
    if channel is None:
        return []
    return [(i.findtext("guid") or i.findtext("link") or "").strip() for i in channel.findall("item")]


# --- JSON Feed 1.1 -------------------------------------------------------------------------------

_JSON_SCHEMA: dict | None = None


def json_schema() -> dict:
    global _JSON_SCHEMA
    if _JSON_SCHEMA is None:
        _JSON_SCHEMA = json.loads((HERE / "schema" / "jsonfeed-1.1.schema.json").read_text(encoding="utf-8"))
    return _JSON_SCHEMA


def parse_json(data: bytes) -> tuple[dict | None, list[Finding]]:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, [err("json-parse", "RFC 8259", f"not JSON: {e}")]
    if not isinstance(doc, dict):
        return None, [err("json-parse", "JSON Feed 1.1, Top-level", "the feed is no JSON object")]
    return doc, []


def check_json(doc: dict, url: str | None, lang: str | None) -> list[Finding]:
    out: list[Finding] = []
    validator = jsonschema.Draft202012Validator(json_schema())
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))[:5]:
        where = "/".join(str(p) for p in e.path) or "(top)"
        out.append(err("json-schema", "JSON Feed 1.1", f"{where}: {e.message}"))
    items = [i for i in doc.get("items", []) if isinstance(i, dict)] if isinstance(doc.get("items"), list) else []

    seen: set[str] = set()
    for n, item in enumerate(items, 1):
        ident = item.get("id")
        if isinstance(ident, str):
            if ident in seen:
                out.append(err("json-id-unique", "JSON Feed 1.1, Items: id", f"id {ident!r} is on two items"))
            seen.add(ident)
        if not isinstance(item.get("content_html"), str) and not isinstance(item.get("content_text"), str):
            out.append(err("json-content", "JSON Feed 1.1, Items", f"item {n} has neither content_html nor content_text"))
        for key in ("date_published", "date_modified"):
            value = item.get(key)
            if isinstance(value, str) and not is_rfc3339(value):
                out.append(err("json-date", "JSON Feed 1.1, Items", f"item {n} {key} {value!r} is not RFC 3339"))

    feed_url = doc.get("feed_url")
    if not isinstance(feed_url, str):
        out.append(err("json-self", "JSON Feed 1.1, Top-level: feed_url", "the feed has no feed_url"))
    elif url and feed_url != url:
        out.append(err("json-self", "JSON Feed 1.1, Top-level: feed_url", f"feed_url is {feed_url!r}, the feed was fetched from {url!r}"))

    for src in [doc, *items]:
        authors = src.get("authors") if isinstance(src.get("authors"), list) else []
        for a in [*authors, src.get("author")]:
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip().lower() in PLACEHOLDER_AUTHORS:
                out.append(err("json-author-name", "JSON Feed 1.1, Authors", f"author name {a['name']!r} names nobody; give the publisher"))

    if lang:
        got = doc.get("language")
        if not isinstance(got, str):
            out.append(warn("json-lang", "JSON Feed 1.1, Top-level: language", f"the feed has no language; expected {lang!r}"))
        elif not same_language(got, lang):
            out.append(err("json-lang", "JSON Feed 1.1, Top-level: language", f"language is {got!r}, the content is {lang!r}"))
    return out


def json_ids(doc: dict) -> list[str]:
    items = doc.get("items") if isinstance(doc.get("items"), list) else []
    return [str(i.get("id", "")) for i in items if isinstance(i, dict)]


# --- documents -----------------------------------------------------------------------------------


def validate(data: bytes, fmt: str | None = None, url: str | None = None, lang: str | None = None) -> list[Finding]:
    """Every document finding of one feed; `url` is where it was fetched (for the self link)."""
    fmt = fmt or sniff(data)
    if fmt == "json":
        doc, out = parse_json(data)
        return out if doc is None else check_json(doc, url, lang)
    root, out = parse_xml(data)
    if root is None:
        return out
    return check_atom(root, url, lang) if fmt == "atom" else check_rss(root, url, lang)


def item_ids(data: bytes, fmt: str | None = None) -> list[str]:
    fmt = fmt or sniff(data)
    if fmt == "json":
        doc, _ = parse_json(data)
        return [] if doc is None else json_ids(doc)
    root, _ = parse_xml(data)
    if root is None:
        return []
    return atom_ids(root) if fmt == "atom" else rss_ids(root)


def paging_links(data: bytes, fmt: str) -> dict[str, str]:
    """RFC 5005 links of a feed as written (relative ones unresolved)."""
    if fmt == "json":
        doc, _ = parse_json(data)
        return {"next": doc["next_url"]} if doc and isinstance(doc.get("next_url"), str) else {}
    root, _ = parse_xml(data)
    if root is None:
        return {}
    holder = root if fmt == "atom" else root.find("channel")
    if holder is None:
        return {}
    return {rel_of(l): l.get("href", "") for l in holder.findall(f"{A}link") if rel_of(l) in PAGING}


def cross_check(feeds: dict[str, bytes]) -> list[Finding]:
    """The feeds of one dataset (format -> body) must list the same item ids in the same order."""
    lists = {fmt: item_ids(body, fmt) for fmt, body in feeds.items()}
    out = []
    names = sorted(lists)
    for other in names[1:]:
        a, b = lists[names[0]], lists[other]
        if a != b:
            first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            out.append(err("cross-ids", "profile T-5201", f"{names[0]} and {other} differ at item {first + 1} ({len(a)} vs {len(b)} items)"))
    return out


# --- HTTP ----------------------------------------------------------------------------------------


class _Refuse(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed) -> None:
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise urllib.error.HTTPError(newurl, code, f"redirect to a host outside the allow-list: {newurl}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Answer:
    status: int
    headers: dict[str, str]
    body: bytes
    too_big: bool = False


def fetch(url: str, headers: dict | None = None, allowed=None, max_bytes: int = MAX_BYTES) -> Answer:
    """GET `url` with a timeout and a size cap; a redirect may only go where `allowed(url)` says."""
    allowed = allowed or (lambda u: is_http_url(u))
    if not allowed(url):
        raise ValueError(f"{url} is outside the allow-list")
    opener = urllib.request.build_opener(_Refuse(allowed))
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        resp = opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        resp = e
    with resp:
        body = resp.read(max_bytes + 1) if resp.fp else b""
        return Answer(resp.status if hasattr(resp, "status") else resp.code, {k.lower(): v for k, v in resp.headers.items()}, body[:max_bytes], len(body) > max_bytes)


def check_http(url: str, fmt: str | None = None, lang: str | None = None, allowed=None, max_bytes: int = MAX_BYTES) -> tuple[list[Finding], bytes]:
    """Document and HTTP findings of the feed at `url`, and its body."""
    try:
        ans = fetch(url, allowed=allowed, max_bytes=max_bytes)
    except (OSError, ValueError) as e:
        return [err("http-status", "RFC 9110 15.3.1", f"{url}: {e}")], b""
    if ans.status != 200:
        return [err("http-status", "RFC 9110 15.3.1", f"{url} answered {ans.status}")], b""
    if ans.too_big:
        return [err("http-size", "profile T-5201", f"{url} is larger than {max_bytes} bytes")], b""
    fmt = fmt or sniff(ans.body)
    out = validate(ans.body, fmt, url, lang)

    ctype = ans.headers.get("content-type", "")
    mtype, _, params = ctype.partition(";")
    if mtype.strip().lower() != MEDIA[fmt] or "charset=" not in params.lower():
        out.append(err("http-content-type", "RFC 4287 7 / JSON Feed 1.1", f"Content-Type is {ctype!r}, expected '{MEDIA[fmt]}; charset=utf-8'"))

    etag, modified = ans.headers.get("etag"), ans.headers.get("last-modified")
    if not etag and not modified:
        out.append(err("http-validator", "RFC 9110 8.8", "neither ETag nor Last-Modified; readers poll the whole feed every time"))
    else:
        cond = {"If-None-Match": etag} if etag else {"If-Modified-Since": modified}
        try:
            again = fetch(url, cond, allowed, max_bytes)
            if again.status != 304:
                out.append(err("http-304", "RFC 9110 13.1", f"a conditional GET with {next(iter(cond))} answered {again.status}, not 304"))
        except (OSError, ValueError) as e:
            out.append(err("http-304", "RFC 9110 13.1", f"the conditional GET failed: {e}"))

    origin = "https://conformance.invalid"
    try:
        cors = fetch(url, {"Origin": origin}, allowed, max_bytes).headers.get("access-control-allow-origin")
    except (OSError, ValueError):
        cors = None
    if cors not in ("*", origin):
        out.append(err("http-cors", "Fetch, CORS protocol", f"Access-Control-Allow-Origin for a foreign Origin is {cors!r}; browsers cannot read the feed"))

    here = item_ids(ans.body, fmt)
    for rel, href in paging_links(ans.body, fmt).items():
        target = urllib.parse.urljoin(url, href)
        if not is_absolute(href):
            out.append(err("http-paging", "RFC 5005 3", f"rel={rel} {href!r} is relative"))
        if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(url).netloc:
            out.append(warn("http-paging", "RFC 5005 3", f"rel={rel} {target!r} is on another host; not followed"))
            continue
        try:
            page = fetch(target, allowed=allowed, max_bytes=max_bytes)
        except (OSError, ValueError) as e:
            out.append(err("http-paging", "RFC 5005 3", f"rel={rel} {target!r}: {e}"))
            continue
        if page.status != 200:
            out.append(err("http-paging", "RFC 5005 3", f"rel={rel} {target!r} answered {page.status}"))
        elif rel == "next" and here and set(here) & set(item_ids(page.body, fmt)):
            out.append(err("http-paging-advance", "RFC 5005 3", f"rel=next {target!r} repeats entries of this page"))
    return out, ans.body


# --- sweep ---------------------------------------------------------------------------------------


class _Alternates(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower().split():
            for fmt, media in MEDIA.items():
                if (a.get("type") or "").split(";")[0].strip().lower() == media and a.get("href"):
                    self.feeds.append((fmt, a["href"]))


def advertised(page_html: str, page_url: str) -> list[tuple[str, str]]:
    parser = _Alternates()
    parser.feed(page_html)
    return [(fmt, urllib.parse.urljoin(page_url, href)) for fmt, href in parser.feeds]


def sweep(base: str, out_path: str, lang: str | None, extra_hosts: list[str], max_datasets: int = 1000) -> int:
    """Validate every feed the catalogue's dataset pages advertise; write one tsv row per feed."""
    base = base.rstrip("/")
    hosts = {urllib.parse.urlsplit(base).hostname, *extra_hosts}

    def allowed(u: str) -> bool:
        p = urllib.parse.urlsplit(u)
        return p.scheme == "https" and p.hostname in hosts

    if not allowed(base):
        raise SystemExit(f"--sweep needs an https catalogue URL, got {base}")
    names: list[str] = []
    while len(names) < max_datasets:
        q = urllib.parse.urlencode({"rows": 100, "start": len(names), "fl": "name"})
        ans = fetch(f"{base}/api/3/action/package_search?{q}", allowed=allowed)
        results = json.loads(ans.body).get("result", {}).get("results", []) if ans.status == 200 else []
        if not results:
            break
        names += [r["name"] for r in results if isinstance(r.get("name"), str)]
    errors = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        tsv = csv.writer(fh, delimiter="\t", lineterminator="\n")
        tsv.writerow(["dataset", "format", "url", "errors", "warnings", "rules"])
        for name in ["-", *names]:
            page_url = f"{base}/" if name == "-" else f"{base}/dataset/{urllib.parse.quote(name)}"
            try:
                page = fetch(page_url, allowed=allowed)
            except OSError as e:
                tsv.writerow(row(name, "page", page_url, [err("http-status", "RFC 9110 15.3.1", str(e))]))
                errors += 1
                continue
            feeds = advertised(page.body.decode("utf-8", "replace"), page_url) if page.status == 200 else []
            bodies: dict[str, bytes] = {}
            for fmt, url in feeds:
                if not allowed(url):
                    findings = [err("http-status", "profile T-5201", f"{url} is outside the catalogue's hosts; not fetched")]
                else:
                    findings, body = check_http(url, fmt, lang, allowed)
                    if body:
                        bodies[fmt] = body
                tsv.writerow(row(name, fmt, url, findings))
                errors += sum(f.level == "error" for f in findings)
            if len(bodies) > 1:
                findings = cross_check(bodies)
                if findings:
                    tsv.writerow(row(name, "cross", "", findings))
                    errors += len(findings)
    return errors


def row(name: str, fmt: str, url: str, findings: list[Finding]) -> list:
    rules = sorted({f.rule for f in findings})
    return [name, fmt, url, sum(f.level == "error" for f in findings), sum(f.level == "warning" for f in findings), ",".join(rules)]


# --- CLI -----------------------------------------------------------------------------------------


def report(label: str, findings: list[Finding]) -> None:
    errors = sum(f.level == "error" for f in findings)
    print(f"{label}: {errors} errors, {len(findings) - errors} warnings")
    for f in findings:
        print(f"  {f}")


def load(src: str, fmt: str | None, lang: str | None, self_url: str | None, max_bytes: int) -> tuple[list[Finding], bytes]:
    if re.match(r"https?://", src):
        return check_http(src, fmt, lang, max_bytes=max_bytes)
    data = pathlib.Path(src).read_bytes()
    return validate(data, fmt, self_url, lang), data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="*", help="a feed URL or file")
    ap.add_argument("--format", choices=sorted(MEDIA), help="skip sniffing")
    ap.add_argument("--lang", help="the content language, e.g. sk")
    ap.add_argument("--self", dest="self_url", help="for a file: the URL its self link must name")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    ap.add_argument("--cross", action="store_true", help="the sources are the feeds of one dataset; compare their item ids")
    ap.add_argument("--sweep", metavar="CKAN_BASE", help="validate every feed the catalogue's dataset pages advertise")
    ap.add_argument("--out", default="feeds-sweep.tsv", help="tsv the sweep writes")
    ap.add_argument("--allow-host", action="append", default=[], help="another host the sweep may fetch from")
    args = ap.parse_args(argv)

    if args.sweep:
        errors = sweep(args.sweep, args.out, args.lang, args.allow_host)
        print(f"{args.out}: {errors} errors")
        return 1 if errors else 0
    if not args.source:
        ap.error("give a feed URL or file, or --sweep")
    failed = False
    bodies: dict[str, bytes] = {}
    for src in args.source:
        findings, body = load(src, args.format, args.lang, args.self_url, args.max_bytes)
        report(src, findings)
        failed |= any(f.level == "error" for f in findings)
        if body:
            bodies[args.format or sniff(body)] = body
    if args.cross:
        findings = cross_check(bodies)
        report("cross-format", findings)
        failed |= bool(findings)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
