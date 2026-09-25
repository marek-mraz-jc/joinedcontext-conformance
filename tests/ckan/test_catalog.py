"""The per-project catalogue check goes red where a city's record falls short (T-2790…T-2793).

The fixtures are records the gateway wrote (platform `dcat_ap_catalog_tests`): a restricted
endpoint under CC BY-SA, a public one under CC0 and a restricted one with no catalogue block.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from catalog import check, licence_code, main, offer, offer_mismatches, themes, uncatalogued

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORE_SHAPES = (FIXTURES / "dcat-ap-3.0.core.shapes.ttl").read_text(encoding="utf-8")


def record(variant: str) -> dict:
    return json.loads((FIXTURES / f"catalog-{variant}.jsonld").read_text(encoding="utf-8"))


def turtle(variant: str) -> str:
    return (FIXTURES / f"catalog-{variant}.ttl").read_text(encoding="utf-8")


@pytest.mark.parametrize("variant", ["organization-cc-by-sa", "public-cc0"])
def test_a_catalogued_record_passes_as_json_ld_and_turtle(variant: str) -> None:
    assert check(record(variant), turtle(variant), CORE_SHAPES, ["en", "sk"]) == []


def test_the_table_reads_licence_and_themes() -> None:
    assert licence_code(record("organization-cc-by-sa")) == "CC_BYSA_4_0"
    assert themes(record("organization-cc-by-sa")) == ["TRAN", "SOCI"]


def test_a_record_without_a_catalogue_names_every_term_it_lacks() -> None:
    missing = uncatalogued(record("restricted-bare"), ["fi", "sv", "en"])
    for term in ("dct:publisher", "dcat:contactPoint", "dct:license", "dcat:theme", "dct:spatial"):
        assert term in missing
    assert "dcat:keyword in 'fi'" in missing


def test_a_language_the_keywords_leave_out_is_named() -> None:
    assert uncatalogued(record("public-cc0"), ["sk", "en", "fi"]) == ["dcat:keyword in 'fi'"]


def test_a_distribution_under_another_licence_is_named() -> None:
    changed = record("public-cc0")
    changed["dcat:distribution"][0]["dct:license"] = (
        "http://publications.europa.eu/resource/authority/licence/CC_BY_4_0"
    )
    assert any("on distribution" in item for item in uncatalogued(changed, ["en"]))


def test_a_contact_that_is_not_a_mailto_address_is_named() -> None:
    changed = record("public-cc0")
    changed["dcat:contactPoint"]["vcard:hasEmail"] = "opendata@city.example.org"
    assert "dcat:contactPoint.vcard:hasEmail as a mailto: address" in uncatalogued(changed, ["en"])


def test_an_unlicensed_record_makes_no_offer_and_the_bare_one_does_not() -> None:
    assert offer(record("restricted-bare")) is None
    assert offer_mismatches(record("restricted-bare")) == []


@pytest.mark.parametrize(
    ("variant", "edit", "finding"),
    [
        ("organization-cc-by-sa", lambda p: p.pop("odrl:duty"), "asks"),
        ("public-cc0", lambda p: p.update({"odrl:duty": [{"odrl:action": "odrl:attribute"}]}), "asks"),
        ("public-cc0", lambda p: p.update({"odrl:action": "odrl:distribute"}), "not odrl:use"),
        ("public-cc0", lambda p: p.update({"odrl:target": "https://elsewhere.example.org/"}), "not the dataset"),
        (
            "public-cc0",
            lambda p: p.update({"odrl:constraint": [{"odrl:leftOperand": "odrl:recipient"}]}),
            "constrains who",
        ),
        (
            "organization-cc-by-sa",
            lambda p: p.update({"odrl:constraint": p["odrl:constraint"][1:]}),
            "does not constrain the recipient",
        ),
    ],
)
def test_an_offer_that_departs_from_its_licence_or_audience_is_named(variant, edit, finding) -> None:
    changed = copy.deepcopy(record(variant))
    edit(offer(changed)["odrl:permission"])
    assert any(finding in item for item in offer_mismatches(changed)), offer_mismatches(changed)


def test_a_licensed_record_without_an_offer_is_named() -> None:
    changed = record("public-cc0")
    changed.pop("odrl:hasPolicy")
    assert offer_mismatches(changed) == ["licence CC0 but no odrl:Offer in odrl:hasPolicy"]


def test_a_turtle_that_says_something_else_is_named() -> None:
    other = turtle("public-cc0").replace("Every event the city lists", "Something else", 1)
    assert "the Turtle and the JSON-LD are not one graph" in check(record("public-cc0"), other, CORE_SHAPES, [])


def test_the_runner_refuses_shapes_that_are_not_the_pinned_release(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "shapes.ttl"
    fake.write_text(CORE_SHAPES, encoding="utf-8")
    monkeypatch.setenv("DCAT_AP_SHACL", str(fake))
    with pytest.raises(SystemExit, match="not the pinned SEMIC"):
        main(["helsinki"])


def test_the_runner_refuses_a_base_that_is_not_https() -> None:
    with pytest.raises(SystemExit, match="https"):
        main(["helsinki", "--base", "http://dev.joinedcontext.com"])
