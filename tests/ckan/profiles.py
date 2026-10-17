#!/usr/bin/env python3
"""Every Endpoint record against the national DCAT-AP profiles (T-4510, EP-27, EP-78).

`dcat.py` holds a record to DCAT-AP 3.0; this holds the same record to each profile whose shapes
are vendored under `dcat/shapes/` (README there: source, version, digest of every file) and
writes what each one reports. DCAT-AP 3.0.0 is a gate: a violation fails. DCAT-AP-SK and
DCAT-AP-CZ are reported, not gated, until T-4499 writes records that meet them; the report is
committed (`evidence/dcat-profiles.md`) and the test holds it equal to a fresh run, so the list
of what a record lacks for a national catalogue is always today's.

    python3 tests/ckan/profiles.py                    report for the committed fixtures
    python3 tests/ckan/profiles.py --write FILE       and write it
    python3 tests/ckan/profiles.py RECORD.jsonld ...  report for these records

Exit 1 when a vendored file is not its pinned digest or a gate profile reports a violation.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

import rdflib
from pyshacl import validate

from dcat import PREFIXES, graph

ROOT = Path(__file__).resolve().parents[2]
SHAPES = ROOT / "dcat" / "shapes"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPORT = ROOT / "evidence" / "dcat-profiles.md"
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
#: "Less than 1 values on <focus>->path", "Node [ … ]->path does not …": the node and path the
#: row already names.
REPEATED = re.compile(r"( on )?(<[^>]*>|Node \[.*\])->\S+ ?")

#: name -> (gate, shapes files under dcat/shapes). Only the profiles every Endpoint record must
#: meet; the vendored HVD and catalogue shapes apply to an HVD dataset and to a catalogue.
PROFILES: dict[str, tuple[bool, tuple[str, ...]]] = {
    "DCAT-AP 3.0.0": (True, ("dcat-ap/3.0.0/dcat-ap-SHACL.ttl",)),
    "DCAT-AP-SK 2.0.1": (False, ("dcat-ap/2.1.1/dcat-ap_2.1.1_shacl_shapes.ttl",
                                 "dcat-ap-sk/2.0.1/sk-additions.shacl.ttl")),
    "DCAT-AP-CZ 2026-09-23": (False, ("dcat-ap-cz/2026-09-23/otevrena-data/shacl.ttl",
                                      "dcat-ap-cz/2026-09-23/cz-additions.shacl.ttl")),
}

#: The upstream files, byte for byte (dcat/shapes/README.md). A local edit that relaxes an
#: official shape fails here before it can pass anything.
DIGESTS = {
    "dcat-ap/3.0.0/dcat-ap-SHACL.ttl":
        "92f76609d78d257123e75bc6b7155df5cc0a63f14c29fbc12b0ac95c56af2059",
    "dcat-ap/2.1.1/dcat-ap_2.1.1_shacl_shapes.ttl":
        "467ace005196e261b9299422e921aba079c279a9e914e3a2d499e048cbc7cd0b",
    "dcat-ap-cz/2026-09-23/otevrena-data/shacl.ttl":
        "3c21e6977a2199e44a7dd6f594311d453f444687f32fe84707ab3c9c4069adc2",
    "dcat-ap-cz/2026-09-23/hvd/shacl.ttl":
        "aa4a4640f60d62c040ffe5a65874d0b388d1ca95c330a31710c9372d7c46d915",
    "dcat-ap-cz/2026-09-23/rozhrani-katalogu-dat/shacl.ttl":
        "7aba774c0519534a46b15bf48c2942f84cac20ef51b2403f0fe882ecd1d61ff6",
}


def digest_mismatches(root: Path = SHAPES) -> list[str]:
    out = []
    for name, want in DIGESTS.items():
        path = root / name
        have = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if have != want:
            out.append(f"{name}: sha256 {have}, pinned {want}")
    return out


def shapes_graph(files: tuple[str, ...], root: Path = SHAPES) -> rdflib.Graph:
    shapes = rdflib.Graph()
    for name in files:
        shapes.parse(root / name, format="turtle")
    return shapes


def violations(record: dict, shapes: rdflib.Graph) -> list[tuple[str, str, str]]:
    """`(focus, path, message)` for every result the shapes report. A blank focus node reads
    `[blank]` and the message drops the node it repeats, so the list is the same on every run."""
    data = graph(record)
    names = shapes.namespace_manager
    for prefix, iri in reversed(PREFIXES.items()):  # the first name of an IRI wins: dct, not dcterms
        names.bind(prefix, iri, override=True, replace=True)
    _, results, _ = validate(data_graph=data, shacl_graph=shapes, advanced=True,
                             inference="none", allow_warnings=False)
    found = []
    for result in results.subjects(rdflib.RDF.type, SH.ValidationResult):
        focus = results.value(result, SH.focusNode)
        path = results.value(result, SH.resultPath)
        message = results.value(result, SH.resultMessage)
        found.append((
            "[blank]" if isinstance(focus, rdflib.BNode) else str(focus),
            "" if path is None or isinstance(path, rdflib.BNode) else names.normalizeUri(path),
            REPEATED.sub("", str(message or "")),
        ))
    return sorted(found)


def run(records: dict[str, dict]) -> dict[str, dict[str, list[tuple[str, str, str]]]]:
    out = {}
    for profile, (_, files) in PROFILES.items():
        shapes = shapes_graph(files)
        out[profile] = {name: violations(record, shapes) for name, record in records.items()}
    return out


def report(results: dict[str, dict[str, list[tuple[str, str, str]]]]) -> str:
    lines = ["# DCAT-AP profiles: what each Endpoint record lacks (T-4510)", "",
             "Written by `python3 tests/ckan/profiles.py --write evidence/dcat-profiles.md`; "
             "`tests/ckan/test_profiles.py` holds it equal to a fresh run. Records: the gateway's "
             "fixtures in `tests/ckan/fixtures/catalog-*.jsonld`. Shapes: `dcat/shapes/` "
             "(README there).", ""]
    for profile, per_record in results.items():
        gate = PROFILES[profile][0]
        total = sum(len(v) for v in per_record.values())
        verdict = "conforms" if total == 0 else f"{total} violation(s)"
        lines += [f"## {profile} ({'gate' if gate else 'reported, gate from T-4499'}): {verdict}", ""]
        for name, found in per_record.items():
            lines.append(f"### {name}: {len(found)}")
            if found:
                lines += ["", "| n | focus | path | message |", "|---|---|---|---|"]
                for (focus, path, message), n in sorted(collections.Counter(found).items()):
                    lines.append(f"| {n} | `{focus}` | `{path}` | {message.replace('|', '/')} |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def fixtures() -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(FIXTURES.glob("catalog-*.jsonld"))}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", nargs="*", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args(argv)
    bad = digest_mismatches()
    for line in bad:
        print(f"not the pinned shapes: {line}", file=sys.stderr)
    records = ({p.name: json.loads(p.read_text(encoding="utf-8")) for p in args.records}
               if args.records else fixtures())
    results = run(records)
    text = report(results)
    if args.write:
        args.write.write_text(text, encoding="utf-8")
    print(text)
    gated = [p for p, (gate, _) in PROFILES.items() if gate and any(results[p].values())]
    return 1 if bad or gated else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
