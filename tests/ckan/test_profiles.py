"""The national DCAT-AP profiles over the gateway's records (T-4510, EP-27, EP-78).

DCAT-AP 3.0.0 is a gate on every fixture; DCAT-AP-SK and DCAT-AP-CZ are reported until T-4499,
and the committed report is today's. The shapes are the vendored files, by digest.
"""

from __future__ import annotations

import copy

import profiles
from profiles import DIGESTS, PROFILES, REPORT, digest_mismatches, fixtures, report, run, shapes_graph, violations


def test_every_vendored_file_is_its_pinned_digest() -> None:
    assert digest_mismatches() == []


def test_an_edited_official_file_is_named(tmp_path) -> None:
    for name in DIGESTS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((profiles.SHAPES / name).read_bytes())
    relaxed = tmp_path / "dcat-ap/3.0.0/dcat-ap-SHACL.ttl"
    relaxed.write_bytes(relaxed.read_bytes() + b"\n# relaxed\n")
    assert [line.split(":")[0] for line in digest_mismatches(tmp_path)] == ["dcat-ap/3.0.0/dcat-ap-SHACL.ttl"]


def test_every_record_meets_dcat_ap_3() -> None:
    shapes = shapes_graph(PROFILES["DCAT-AP 3.0.0"][1])
    for name, record in fixtures().items():
        assert violations(record, shapes) == [], name


def test_a_record_without_a_title_fails_the_gate() -> None:
    record = copy.deepcopy(fixtures()["catalog-public-cc0.jsonld"])
    del record["dct:title"]
    found = violations(record, shapes_graph(PROFILES["DCAT-AP 3.0.0"][1]))
    assert any(path == "dct:title" for _, path, _ in found), found


def test_the_national_profiles_report_what_the_records_lack() -> None:
    results = run(fixtures())
    sk = {path for found in results["DCAT-AP-SK 2.0.1"].values() for _, path, _ in found}
    cz = {path for found in results["DCAT-AP-CZ 2026-09-23"].values() for _, path, _ in found}
    # The two rules every record misses today: the terms-of-use block of each national catalogue
    # and, for the Czech one, the legislation the dataset is published under.
    assert "leg:termsOfUse" in sk
    assert {"pu:specifikace", "r5r:applicableLegislation"} <= cz


def test_the_committed_report_is_todays() -> None:
    assert REPORT.read_text(encoding="utf-8") == report(run(fixtures())), (
        "rerun: python3 tests/ckan/profiles.py --write evidence/dcat-profiles.md"
    )
