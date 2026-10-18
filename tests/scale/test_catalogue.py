"""The exported catalogue (T-4544): the platform generator's stream, and packages CKAN's
`package_create` accepts (its validators: ckan/logic/validators.py, ckan 2.11)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import catalogue  # noqa: E402

NAME = re.compile(r"[a-z0-9_-]{2,100}")  # name_validator, PACKAGE_NAME_MAX_LENGTH
TAG = re.compile(r"[\w \-.]{2,100}")  # tag_name_validator, tag_length_validator


def test_seed_4497_is_the_gateway_measurements_catalogue():
    # read from the Rust stream (mod scale, branch agent/worker-2/T-4492) with the same seed
    first = [(d.entity_type, d.place, d.year) for d in catalogue.catalogue(4497, 5)]
    assert first == [
        ("ParkingSpot", "Hrochoť", 2023),
        ("ParkingSpot", "Kriváň", 2023),
        ("WasteContainer", "Pitelová", 2025),
        ("NoiseLevelObserved", "Lenartovce", 2025),
        ("Streetlight", "Leváre", 2024),
    ]
    last = list(catalogue.catalogue(4497, 10_000))[-1]
    assert (last.entity_type, last.place, last.year) == ("KeyPerformanceIndicator", "Olováry", 2026)


def test_every_package_is_one_package_create_accepts():
    datasets = list(catalogue.catalogue(4497, 10_000))
    packages = [catalogue.package(d) for d in datasets]
    orgs = {o["name"] for o in catalogue.organizations(datasets)}
    names = [p["name"] for p in packages]
    assert len(set(names)) == len(names), "a name is unique"
    for p in packages:
        assert NAME.fullmatch(p["name"]), p["name"]
        assert p["owner_org"] in orgs and NAME.fullmatch(p["owner_org"]), p["owner_org"]
        assert p["title"] and p["notes"]
        for tag in p["tags"]:
            assert TAG.fullmatch(tag["name"]), tag["name"]
        keys = [e["key"] for e in p["extras"]]
        assert len(set(keys)) == len(keys)
        assert all(r["url"].startswith("https://") for r in p["resources"])
    # a place with a parenthesis still makes a tag (`Lešť (vojenský obvod)` is on dev)
    assert any(t["name"] == "Lešť vojenský obvod" for p in packages for t in p["tags"])
    assert len(orgs) < len(packages)


def test_the_dcat_catalogue_names_every_dataset_in_both_languages():
    datasets = list(catalogue.catalogue(7, 20))
    doc = catalogue.dcat(datasets)
    assert doc["@type"] == "dcat:Catalog"
    assert len(doc["dcat:dataset"]) == 20
    one = doc["dcat:dataset"][0]
    assert {t["@language"] for t in one["dct:title"]} == {"sk", "en"}
    assert one["dcat:distribution"][0]["dcat:mediaType"] == "application/json"
