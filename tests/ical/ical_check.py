#!/usr/bin/env python3
"""RFC 5545 checks of an iCalendar feed, and of the Event mapping a view serves it from (T-5044).

    ical_check.py FILE_OR_URL [...]                     structure of each feed
    ical_check.py --events NGSILD_ROOT FILE_OR_URL      also the Event mapping against the view

Every finding names its rule, so a test can ask for exactly the one a broken file breaks:

    crlf          a line break other than CRLF (3.1)
    fold          a line longer than 75 octets, not folded (3.1)
    syntax        a content line that is not `name *(;param) : value` (3.1)
    vcalendar     not one VCALENDAR from the first line to the last (3.4)
    nesting       a BEGIN without its END, or an END of another component (3.6)
    version       no VERSION:2.0 (3.7.4)
    prodid        no PRODID (3.7.3)
    uid           a VEVENT without UID (3.6.1)
    uid-unique    two VEVENTs with one UID (3.8.4.7)
    dtstamp       a VEVENT without DTSTAMP, or one not in UTC (3.8.7.2)
    dtstart       a VEVENT without DTSTART, or one that is no DATE or DATE-TIME (3.6.1, 3.3.4, 3.3.5)
    dtend         DTEND not after DTSTART, of another value type, or beside DURATION (3.8.2.2)
    escape        an unescaped `,` or `;`, or a `\\` before another character, in a TEXT value (3.3.11)
    tzid          a TZID no VTIMEZONE of the calendar defines (3.2.19)
    geo           GEO not `latitude;longitude` in range (3.8.1.6)
    url           URL not an absolute http(s) URI (3.8.4.6)

The mapping rules (Antares `docs/analysis/ical-mapping.md`, EP-125, EP-126): every Event of the view with
a `startDate` and a `name` is one VEVENT whose UID is its id (`event-missing`), and nothing else is
(`event-extra`); SUMMARY is its name (`event-summary`); an `address` gives a LOCATION
(`event-location`); an http(s) `url` is the URL (`event-url`).

Prints one line per feed and one per finding; exits 1 on any finding. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

UA = {"User-Agent": "joinedcontext-conformance ical (T-5044)"}
MAX_BYTES = 32 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9-]+")
# param = name "=" value *("," value); a value is quoted or holds no ";", ":" or ","
PARAM = re.compile(r';([A-Za-z0-9-]+)=((?:"[^"]*"|[^";:,]*)(?:,(?:"[^"]*"|[^";:,]*))*)')
TEXT = {"SUMMARY", "DESCRIPTION", "LOCATION", "COMMENT", "CONTACT"}
DATE = re.compile(r"\d{8}")
DATE_TIME = re.compile(r"\d{8}T\d{6}Z?")


@dataclass
class Finding:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


@dataclass
class Prop:
    name: str
    params: dict[str, str]
    value: str


@dataclass
class Component:
    name: str
    props: list[Prop] = field(default_factory=list)
    children: list[Component] = field(default_factory=list)

    def get(self, name: str) -> Prop | None:
        return next((p for p in self.props if p.name == name), None)


def content_lines(data: bytes) -> tuple[list[str], list[Finding]]:
    """The unfolded content lines, and the findings of their framing (crlf, fold)."""
    findings = []
    if re.search(rb"(?<!\r)\n|\r(?!\n)", data):
        findings.append(Finding("crlf", "a line break other than CRLF (RFC 5545 3.1)"))
    physical = re.split(rb"\r\n|\n|\r", data)
    if physical and physical[-1] == b"":
        physical.pop()
    long = [i + 1 for i, line in enumerate(physical) if len(line) > 75]
    if long:
        findings.append(
            Finding(
                "fold",
                f"line {long[0]} holds over 75 octets, not folded ({len(long)} lines; RFC 5545 3.1)",
            )
        )
    text = b"\r\n".join(physical).decode("utf-8", errors="replace")
    return re.sub(r"\r\n[ \t]", "", text).split("\r\n"), findings


def parse_line(line: str) -> Prop | None:
    m = NAME.match(line)
    if not m:
        return None
    rest, params = line[m.end() :], {}
    while rest.startswith(";"):
        p = PARAM.match(rest)
        if not p:
            return None
        params[p.group(1).upper()] = p.group(2).strip('"')
        rest = rest[p.end() :]
    if not rest.startswith(":"):
        return None
    return Prop(m.group().upper(), params, rest[1:])


def parse(data: bytes) -> tuple[list[Component], list[Finding]]:
    """The top-level components of a feed, and every framing and syntax finding."""
    lines, findings = content_lines(data)
    while lines and lines[-1] == "":
        lines.pop()
    roots: list[Component] = []
    stack: list[Component] = []
    for n, line in enumerate(lines, 1):
        prop = parse_line(line)
        if prop is None:
            findings.append(
                Finding(
                    "syntax",
                    f"content line {n} is not name:value: {line[:60]!r} (RFC 5545 3.1)",
                )
            )
            continue
        if prop.name == "BEGIN":
            comp = Component(prop.value.upper())
            (stack[-1].children if stack else roots).append(comp)
            stack.append(comp)
        elif prop.name == "END":
            if not stack or stack[-1].name != prop.value.upper():
                open_ = stack[-1].name if stack else "nothing"
                findings.append(
                    Finding(
                        "nesting",
                        f"END:{prop.value} at line {n} closes {open_} (RFC 5545 3.6)",
                    )
                )
            else:
                stack.pop()
        elif stack:
            stack[-1].props.append(prop)
        else:
            findings.append(
                Finding(
                    "vcalendar",
                    f"line {n} {prop.name} outside any component (RFC 5545 3.4)",
                )
            )
    for comp in stack:
        findings.append(
            Finding("nesting", f"BEGIN:{comp.name} without END (RFC 5545 3.6)")
        )
    return roots, findings


def unescape(value: str) -> str:
    return re.sub(
        r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value
    )


def escape_errors(prop: Prop) -> bool:
    """True when a TEXT value holds an unescaped `,`/`;` or a backslash before another character."""
    rest = re.sub(r"\\[\\;,nN]", "", prop.value)
    return bool(re.search(r"[,;\\]", rest))


def when(prop: Prop) -> tuple[str, datetime | date] | None:
    """(kind, value) of a DATE or DATE-TIME property: kind is date, utc, floating or tz:<TZID>."""
    v = prop.value
    try:
        if prop.params.get("VALUE", "").upper() == "DATE" or (
            DATE.fullmatch(v) and "T" not in v
        ):
            return (
                ("date", datetime.strptime(v, "%Y%m%d").date())
                if DATE.fullmatch(v)
                else None
            )
        if not DATE_TIME.fullmatch(v):
            return None
        t = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if v.endswith("Z"):
        return "utc", t
    return (f"tz:{prop.params['TZID']}" if "TZID" in prop.params else "floating"), t


def event_findings(n: int, ev: Component, tzids: set[str]) -> list[Finding]:
    out = []
    where = f"VEVENT {n}"
    if ev.get("UID") is None:
        out.append(Finding("uid", f"{where} without UID (RFC 5545 3.6.1)"))
    stamp = ev.get("DTSTAMP")
    if stamp is None or (when(stamp) or ("",))[0] != "utc":
        out.append(
            Finding(
                "dtstamp",
                f"{where}: DTSTAMP missing or not a UTC DATE-TIME (RFC 5545 3.8.7.2)",
            )
        )
    start = ev.get("DTSTART")
    s = when(start) if start else None
    if s is None:
        out.append(
            Finding(
                "dtstart",
                f"{where}: DTSTART missing or no DATE/DATE-TIME (RFC 5545 3.6.1)",
            )
        )
    end = ev.get("DTEND")
    if end is not None:
        e = when(end)
        if ev.get("DURATION") is not None:
            out.append(
                Finding("dtend", f"{where}: DTEND beside DURATION (RFC 5545 3.6.1)")
            )
        elif e is None:
            out.append(
                Finding(
                    "dtend", f"{where}: DTEND is no DATE/DATE-TIME (RFC 5545 3.8.2.2)"
                )
            )
        elif s is None:
            pass  # reported as dtstart
        elif (e[0] == "date") != (s[0] == "date"):
            out.append(
                Finding(
                    "dtend",
                    f"{where}: DTEND of another value type than DTSTART (RFC 5545 3.8.2.2)",
                )
            )
        elif e[0] == s[0] and e[1] <= s[1]:
            out.append(
                Finding(
                    "dtend",
                    f"{where}: DTEND {end.value} not after DTSTART {start.value} (RFC 5545 3.8.2.2)",
                )
            )
    for prop in ev.props:
        if prop.name in TEXT and escape_errors(prop):
            out.append(
                Finding(
                    "escape",
                    f"{where}: {prop.name} holds an unescaped , ; or \\ (RFC 5545 3.3.11)",
                )
            )
        tzid = prop.params.get("TZID")
        if tzid is not None and tzid not in tzids:
            out.append(
                Finding(
                    "tzid",
                    f"{where}: {prop.name} names TZID {tzid!r}, no VTIMEZONE defines it (RFC 5545 3.2.19)",
                )
            )
    geo = ev.get("GEO")
    if geo is not None:
        try:
            lat, lon = (float(x) for x in geo.value.split(";"))
            ok = -90 <= lat <= 90 and -180 <= lon <= 180
        except ValueError:
            ok = False
        if not ok:
            out.append(
                Finding(
                    "geo",
                    f"{where}: GEO {geo.value!r} is not latitude;longitude (RFC 5545 3.8.1.6)",
                )
            )
    url = ev.get("URL")
    if url is not None:
        u = urllib.parse.urlsplit(url.value)
        if (
            u.scheme not in ("http", "https")
            or not u.netloc
            or re.search(r"\s", url.value)
        ):
            out.append(
                Finding(
                    "url",
                    f"{where}: URL {url.value[:60]!r} is not an absolute http(s) URI (RFC 5545 3.8.4.6)",
                )
            )
    return out


def validate(data: bytes) -> list[Finding]:
    """Every RFC 5545 finding of one feed."""
    roots, findings = parse(data)
    if len(roots) != 1 or roots[0].name != "VCALENDAR":
        return findings + [
            Finding(
                "vcalendar",
                f"{len(roots)} top-level components, not one VCALENDAR (RFC 5545 3.4)",
            )
        ]
    cal = roots[0]
    version = cal.get("VERSION")
    if version is None or version.value != "2.0":
        findings.append(
            Finding("version", "VCALENDAR without VERSION:2.0 (RFC 5545 3.7.4)")
        )
    if cal.get("PRODID") is None:
        findings.append(Finding("prodid", "VCALENDAR without PRODID (RFC 5545 3.7.3)"))
    tzids = {
        tz.get("TZID").value
        for tz in cal.children
        if tz.name == "VTIMEZONE" and tz.get("TZID")
    }
    events = [c for c in cal.children if c.name == "VEVENT"]
    seen: dict[str, int] = {}
    for n, ev in enumerate(events, 1):
        findings += event_findings(n, ev, tzids)
        uid = ev.get("UID")
        if uid is not None:
            if uid.value in seen:
                findings.append(
                    Finding(
                        "uid-unique",
                        f"VEVENT {n} repeats the UID of VEVENT {seen[uid.value]} (RFC 5545 3.8.4.7)",
                    )
                )
            seen.setdefault(uid.value, n)
    return findings


def vevents(data: bytes) -> list[Component]:
    roots, _ = parse(data)
    return [c for r in roots for c in r.children if c.name == "VEVENT"]


def plain(value):
    """A keyValues member as one value: a typed literal's @value, a LanguageMap's first text."""
    if isinstance(value, dict):
        if "@value" in value:
            return value["@value"]
        if "value" in value:
            return plain(value["value"])
        if "type" not in value and value:
            return plain(next(iter(value.values())))
    return value


def member(entity: dict, name: str):
    """A keyValues member by its local name (`name` or an expanded IRI ending in it)."""
    for key, value in entity.items():
        if key == name or re.split(r"[/#]", key)[-1] == name:
            return plain(value)
    return None


def mapping_findings(data: bytes, entities: list[dict]) -> list[Finding]:
    """The feed against the view's Events (keyValues), as the Antares mapping renders them."""
    by_uid = {}
    for ev in vevents(data):
        uid = ev.get("UID")
        if uid is not None:
            by_uid[unescape(uid.value)] = ev
    findings, expected = [], set()
    for ent in entities:
        eid, name, start = (
            ent.get("id", ""),
            member(ent, "name"),
            member(ent, "startDate"),
        )
        if not isinstance(name, str) or not name or not start:
            continue
        expected.add(eid)
        ev = by_uid.get(eid)
        if ev is None:
            findings.append(
                Finding(
                    "event-missing",
                    f"Event {eid} has a name and a startDate, the feed has no VEVENT for it",
                )
            )
            continue
        summary = ev.get("SUMMARY")
        if summary is None or unescape(summary.value) != name:
            findings.append(
                Finding(
                    "event-summary",
                    f"VEVENT {eid}: SUMMARY is not the Event's name {name[:40]!r}",
                )
            )
        if member(ent, "address") and ev.get("LOCATION") is None:
            findings.append(
                Finding(
                    "event-location",
                    f"VEVENT {eid}: the Event has an address, the VEVENT no LOCATION",
                )
            )
        url = member(ent, "url")
        if (
            isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and not re.search(r"\s", url)
        ):
            got = ev.get("URL")
            if got is None or got.value != url:
                findings.append(
                    Finding(
                        "event-url",
                        f"VEVENT {eid}: URL is not the Event's url {url[:60]!r}",
                    )
                )
    for uid in sorted(set(by_uid) - expected):
        findings.append(
            Finding(
                "event-extra",
                f"VEVENT {uid}: no Event of the view with a name and a startDate has this id",
            )
        )
    return findings


def fetch(url: str, accept: str = "text/calendar") -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={**UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(MAX_BYTES), {k.lower(): v for k, v in r.headers.items()}


def view_events(ngsild: str, pages: int = 50) -> list[dict]:
    """Every Event of the view, keyValues, following the `Link rel=next` pages."""
    url = f"{ngsild.rstrip('/')}/entities?type=Event&format=keyValues&limit=1000"
    out: list[dict] = []
    for _ in range(pages):
        body, headers = fetch(url, "application/json")
        out += json.loads(body)
        nxt = re.search(r'<([^>]+)>;\s*rel="?next"?', headers.get("link", ""))
        if not nxt:
            return out
        url = urllib.parse.urljoin(url, nxt.group(1))
    raise ValueError(f"the view holds over {pages * 1000} Events")


def read(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        return fetch(source)[0]
    with open(source, "rb") as f:
        return f.read()


def main(argv: list[str]) -> int:
    ngsild = None
    if argv[:1] == ["--events"] and len(argv) >= 3:
        ngsild, argv = argv[1], argv[2:]
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    rc = 0
    for source in argv:
        try:
            data = read(source)
            findings = validate(data)
            if ngsild:
                findings += mapping_findings(data, view_events(ngsild))
            n = len(vevents(data))
        except (
            OSError,
            ValueError,
        ) as err:  # a network or parse failure is a result, not a crash
            findings, n = [Finding("fetch", f"{type(err).__name__}: {err}")], 0
        print(
            f"- ical {source}: {n} VEVENT, {'ok' if not findings else f'{len(findings)} findings'}"
        )
        for f in findings[:20]:
            print(f"  - {f}")
        rc |= 1 if findings else 0
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
