"""`$filter` against NGSI-LD `q` through both doors of one view (surfaces-conformance.md section 4,
`test_filter_parity_with_q`).

Each row of the `$filter` mapping table (Antares `odata-linkml-mapping.md` section 5.2) that the
rig's data can hold is asked through `/x/views/{view}/odata/v4` and through
`/x/views/{view}/ngsi-ld/v1`; the two answer the same Entity ids, under view `all` and under view
`parity-restricted`. Rows the design maps but the build still answers 501 are listed in `LATER`
and checked to stay 501, so the row that starts working fails here until it moves into `ROWS`.
Rejected rows answer 501 with the OData error object. Rows the rig holds no data for (string
functions F9 and F15, complex paths F20, ListProperty F22, LanguageProperty F31, VocabProperty
F32, the navigation hop F24) are the crate's own tests (Antares `filter_props.rs`, `odata_query.rs`,
`odata_filter_rows.rs`).
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from conftest import ENTITY_SET, is_odata_error, ngsild_of

ROWS = [
    ("F1", "NO2 eq 40", {"q": "NO2==40"}),
    ("F2", "NO2 eq null", {"q": "!NO2"}),
    ("F3", "NO2 ne 40", {"q": "NO2!=40"}),
    ("F4", "NO2 ne null", {"q": "NO2"}),
    ("F5", "temperature gt 10", {"q": "temperature>10"}),
    ("F5", "temperature ge 11.5", {"q": "temperature>=11.5"}),
    ("F5", "NO2 lt 31", {"q": "NO2<31"}),
    ("F5", "NO2 le 31", {"q": "NO2<=31"}),
    ("F6", "10 lt temperature", {"q": "temperature>10"}),
    ("F7", "temperature gt 10 and NO2 lt 40", {"q": "temperature>10;NO2<40"}),
    ("F7", "temperature lt 10 or NO2 eq 40", {"q": "temperature<10|NO2==40"}),
    ("F8", "(temperature lt 10 or NO2 eq 40) and NO2 gt 30", {"q": "(temperature<10|NO2==40);NO2>30"}),
    ("F10", "not (NO2 eq null)", {"q": "NO2"}),
    ("F12", "NO2 in (22, 40)", {"q": "NO2==22,40"}),
    ("F26", "temperature eq 9.0", {"q": "temperature==9.0"}),
    ("F28", "id eq 'urn:ngsi-ld:AirQualityObserved:bb-1'", {"id": "urn:ngsi-ld:AirQualityObserved:bb-1"}),
    (
        "F28",
        "id in ('urn:ngsi-ld:AirQualityObserved:bb-1','urn:ngsi-ld:AirQualityObserved:ba-1') and NO2 gt 30",
        {"id": "urn:ngsi-ld:AirQualityObserved:bb-1,urn:ngsi-ld:AirQualityObserved:ba-1", "q": "NO2>30"},
    ),
    (
        "F21",
        "temperature/@NgsiLd.observedAt ge 2026-05-01T08:30:00Z",
        {"q": "temperature.observedAt>=2026-05-01T08:30:00Z"},
    ),
    (
        "F29",
        "geo.intersects(location, geography'SRID=4326;POLYGON((19 48,20 48,20 49,19 49,19 48))')",
        {
            "georel": "intersects",
            "geometry": "Polygon",
            "coordinates": "[[[19,48],[20,48],[20,49],[19,49],[19,48]]]",
            "geoproperty": "location",
        },
    ),
]

# mapped in the design, 501 in the build
LATER: list[tuple[str, str]] = []

REJECTED = [
    ("F11", "not (temperature gt 10)"),
    ("F13", "NO2 has 1"),
    ("F14", "NO2 add 1 gt 2"),
    ("F16", "matchesPattern(id,'a')"),
    ("F17", "length(id) gt 1"),
    ("F18", "year(temperature/@NgsiLd.observedAt) eq 2026"),
    ("F19", "round(temperature) eq 9"),
    ("F23", "scope/all(s: s eq '/SK/BB')"),
    ("F25", "temperature gt NO2"),
    ("F27", "NO2 eq duration'P1D'"),
    ("F30", "geo.distance(location, geography'SRID=4326;POINT(19 48)') lt 1"),
    ("F33", "createdAt gt 2026-01-01T00:00:00Z"),
]


def filtered(http, service_root: str, expression: str):
    # `$` stays literal (`%24filter` is a custom option) and so does `=` (the ABNF's EQ is "=" only)
    encoded = quote(expression, safe="=;'(),:/@")
    return http.get(f"{service_root}/{ENTITY_SET}?$filter={encoded}&$select=id")


# the members view `parity-restricted` hides (Antares `ci/views/parity-restricted.json`)
HIDDEN = ("NO2",)


@pytest.fixture(scope="module", params=["all", "parity-restricted"])
def door(request, odata_url, restricted_url):
    return (odata_url, ()) if request.param == "all" else (restricted_url, HIDDEN)


@pytest.mark.parametrize(("row", "expression", "params"), ROWS, ids=[f"{r[0]} {r[1]}" for r in ROWS])
def test_filter_parity_with_q(http, door, row, expression, params):
    door, hidden = door
    a = filtered(http, door, expression)
    if any(name in expression for name in hidden):
        # a member the view hides is not in its model: the name is unknown, as on the
        # NGSI-LD door, where a hidden Attribute reads as absent (threat model V1)
        assert a.status_code == 400, f"{row}: {a.status_code} {a.text}"
        assert is_odata_error(a), a.text
        return
    b = http.get(f"{ngsild_of(door)}/entities", params={"type": ENTITY_SET, **params})
    assert a.status_code == 200, f"{row}: {a.text}"
    assert b.status_code == 200, f"{row}: {b.text}"
    via_odata = sorted(e["id"] for e in a.json()["value"])
    via_q = sorted(e["id"] for e in b.json())
    assert via_odata == via_q, row


def test_parity_rows_select_something(http, odata_url):
    """Not every row empty: a seed that lost its data would pass the parity vacuously."""
    sizes = [len(filtered(http, odata_url, expression).json()["value"]) for _, expression, _ in ROWS]
    assert sum(1 for n in sizes if n) >= len(ROWS) - 1, sizes


@pytest.mark.parametrize(("row", "expression"), LATER + REJECTED, ids=[r[0] for r in LATER + REJECTED])
def test_unmapped_filter_rows_are_501(http, odata_url, row, expression):
    resp = filtered(http, odata_url, expression)
    assert resp.status_code == 501, f"{row}: {resp.status_code} {resp.text}"
    assert is_odata_error(resp), resp.text
