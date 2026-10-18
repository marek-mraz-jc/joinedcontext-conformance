"""T-4497's generated catalogue of 10,000 datasets, exported for CKAN and as DCAT-AP (T-4544).

The generator is the platform's `mod scale` (context-gateway tests/mcp_hub_catalog_tests.rs,
branch agent/worker-2/T-4492) line for line: the same 14 SDM topics, the 518 place names read
from dev's AdministrativeArea entities (`fixtures/places-dev.txt`, copied from that branch),
the same years and the same xorshift64* stream, so seed 4497 gives the same titles here as in
the gateway's measurement.

    python3 tests/scale/catalogue.py ckan --seed 4497 -n 10000 > packages.jsonl
    python3 tests/scale/catalogue.py dcat --seed 4497 -n 10000 > catalogue.jsonld
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterator, NamedTuple

# (type, en topic, sk topic, sk publisher prefix)
TOPICS: tuple[tuple[str, str, str, str], ...] = (
    ("AirQualityObserved", "Air quality", "Kvalita ovzdušia", "SHMÚ"),
    ("WeatherObserved", "Weather observations", "Meteorologické pozorovania", "SHMÚ"),
    ("Event", "Events", "Podujatia", "Mesto"),
    ("BikeHireDockingStation", "Bike sharing", "Zdieľané bicykle", "Mesto"),
    ("ParkingSpot", "Parking spots", "Parkovacie miesta", "Mesto"),
    ("TrafficFlowObserved", "Traffic counts", "Sčítanie dopravy", "Kraj"),
    ("KeyPerformanceIndicator", "Population statistics", "Štatistika obyvateľstva", "ŠÚ SR"),
    ("PointOfInterest", "Points of interest", "Body záujmu", "Mesto"),
    ("WasteContainer", "Waste containers", "Kontajnery na odpad", "Mesto"),
    ("Alert", "Weather warnings", "Výstrahy pred počasím", "SHMÚ"),
    ("NoiseLevelObserved", "Noise levels", "Hladina hluku", "Mesto"),
    ("WaterQualityObserved", "Water quality", "Kvalita vody", "Kraj"),
    ("Streetlight", "Street lighting", "Verejné osvetlenie", "Mesto"),
    ("Vehicle", "Public transport vehicles", "Vozidlá MHD", "Dopravný podnik"),
)
YEARS = (2023, 2024, 2025, 2026)
MASK = (1 << 64) - 1
# Where the generated datasets' distributions point; never resolved by the rig.
BASE = "https://data.example.org"


class Dataset(NamedTuple):
    index: int
    entity_type: str
    title: dict[str, str]
    description: dict[str, str]
    publisher: str
    place: str
    year: int


def places() -> list[str]:
    text = (Path(__file__).parent / "fixtures" / "places-dev.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def _next(state: int) -> tuple[int, int]:
    """xorshift64*: the new state and the value it yields, as the Rust `next`."""
    state ^= state >> 12
    state ^= (state << 25) & MASK
    state ^= state >> 27
    return state, (state * 0x2545_F491_4F6C_DD1D) & MASK


def catalogue(seed: int, n: int) -> Iterator[Dataset]:
    names = places()
    state = max(seed, 1)
    for i in range(n):
        state, a = _next(state)
        state, b = _next(state)
        state, c = _next(state)
        entity_type, en, sk, publisher = TOPICS[a % len(TOPICS)]
        at = names[b % len(names)]
        year = YEARS[c % len(YEARS)]
        by = f"{publisher} {at}" if publisher in ("Mesto", "Kraj") else publisher
        yield Dataset(
            index=i,
            entity_type=entity_type,
            title={"en": f"{en} {at} {year}", "sk": f"{sk} {at} {year}"},
            description={
                "sk": f"{sk}, {at}, rok {year}. Zverejňuje {by}.",
                "en": f"{en} in {at}, {year}, published by {by}.",
            },
            publisher=by,
            place=at,
            year=year,
        )


def slug(text: str, limit: int = 100) -> str:
    """A CKAN name: lowercase ASCII letters, digits, `-` and `_`, at most `limit` long."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return out[:limit].rstrip("-") or "x"


def tag(text: str) -> str:
    """A CKAN tag: word characters, spaces, `-` and `.`, at most 100 long."""
    return " ".join(re.sub(r"[^\w \-.]", " ", text).split())[:100]


def organizations(datasets: list[Dataset]) -> list[dict[str, Any]]:
    """One CKAN organization per publisher, for `organization_create`."""
    seen = {d.publisher for d in datasets}
    return [{"name": slug(p), "title": p} for p in sorted(seen)]


def package(d: Dataset) -> dict[str, Any]:
    """One dataset as `package_create` takes it: the sk title and description (the portal's
    language), the en ones as extras, and one NGSI-LD distribution."""
    url = f"{BASE}/ngsi-ld/v1/entities?type={d.entity_type}"
    return {
        "name": f"d{d.index:05d}-{slug(d.title['sk'], 80)}",
        "title": d.title["sk"],
        "notes": d.description["sk"],
        "owner_org": slug(d.publisher),
        "license_id": "cc-by",
        "tags": [{"name": tag(t)} for t in (d.entity_type, d.place, str(d.year))],
        "extras": [
            {"key": "title_en", "value": d.title["en"]},
            {"key": "notes_en", "value": d.description["en"]},
            {"key": "spatial_text", "value": d.place},
            {"key": "temporal_start", "value": f"{d.year}-01-01"},
            {"key": "temporal_end", "value": f"{d.year}-12-31"},
        ],
        "resources": [{"name": "NGSI-LD", "url": url, "format": "JSON", "mimetype": "application/json"}],
    }


def dcat(datasets: list[Dataset]) -> dict[str, Any]:
    """The catalogue as one DCAT-AP JSON-LD document."""
    return {
        "@context": {
            "dcat": "http://www.w3.org/ns/dcat#",
            "dct": "http://purl.org/dc/terms/",
            "foaf": "http://xmlns.com/foaf/0.1/",
        },
        "@id": f"{BASE}/catalogue",
        "@type": "dcat:Catalog",
        "dct:title": "T-4497 generated catalogue",
        "dcat:dataset": [
            {
                "@id": f"{BASE}/dataset/d{d.index:05d}",
                "@type": "dcat:Dataset",
                "dct:title": [{"@value": v, "@language": k} for k, v in d.title.items()],
                "dct:description": [{"@value": v, "@language": k} for k, v in d.description.items()],
                "dct:publisher": {"@type": "foaf:Agent", "foaf:name": d.publisher},
                "dct:spatial": {"@type": "dct:Location", "dct:title": d.place},
                "dct:temporal": {
                    "@type": "dct:PeriodOfTime",
                    "dcat:startDate": f"{d.year}-01-01",
                    "dcat:endDate": f"{d.year}-12-31",
                },
                "dcat:keyword": [d.entity_type, d.place],
                "dcat:distribution": [
                    {
                        "@type": "dcat:Distribution",
                        "dcat:accessURL": {"@id": f"{BASE}/ngsi-ld/v1/entities?type={d.entity_type}"},
                        "dcat:mediaType": "application/json",
                    }
                ],
            }
            for d in datasets
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("format", choices=("ckan", "organizations", "dcat"))
    parser.add_argument("--seed", type=int, default=4497)
    parser.add_argument("-n", type=int, default=10_000)
    args = parser.parse_args(argv)
    datasets = list(catalogue(args.seed, args.n))
    if args.format == "dcat":
        json.dump(dcat(datasets), sys.stdout, ensure_ascii=False)
    else:
        rows = organizations(datasets) if args.format == "organizations" else map(package, datasets)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
