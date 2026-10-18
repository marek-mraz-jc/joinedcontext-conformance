"""The surface <-> resource comparison of `surfaces.py` (T-3888, EP-108, EP-110, EP-69), offline on a
recorded `package_show`, so the fast lane proves each check can go red."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from surfaces import SURFACES, check_endpoint, compare, percentiles, served

PACKAGE = json.loads((Path(__file__).parent / "fixtures/package-show-surfaces.json").read_text())["result"]
URL = "https://api.dev.joinedcontext.com/api/endpoint/k7m2q4hkiair"
PUBLIC = {"slug": "k7m2q4hkiair", "audience": "public", "publish": {"ckan": {"name": "hki-air"}}}


def without(package, suffix):
    trimmed = copy.deepcopy(package)
    trimmed["resources"] = [r for r in trimmed["resources"] if not r["url"].endswith(suffix)]
    return trimmed


def test_the_default_set_is_every_standard_but_sta_and_the_always_two_stay():
    assert served(PUBLIC) == set(SURFACES) - {"sta"}
    assert served({**PUBLIC, "disabledRepresentations": ["websocket", "csv"]}) == set(SURFACES) - {"sta", "websocket", "csv"}
    assert served({**PUBLIC, "enabledRepresentations": ["csv"]}) == {"csv", "ngsi-ld", "mcp"}
    assert "mcp" not in served({**PUBLIC, "mcp": False})
    assert served({**PUBLIC, "disabledRepresentations": ["ngsi-ld"]}) >= {"ngsi-ld"}


def test_a_dataset_that_lists_every_served_surface_passes():
    assert compare(PUBLIC, URL, PACKAGE) == []


def test_a_missing_surface_resource_fails():
    assert compare(PUBLIC, URL, without(PACKAGE, "/ws")) == ["websocket: no resource at /ws"]


def test_a_resource_for_a_surface_the_endpoint_no_longer_serves_fails():
    spec = {**PUBLIC, "disabledRepresentations": ["odata"]}
    assert compare(spec, URL, PACKAGE) == ["odata: listed and not served"]


def test_a_resource_for_a_removed_file_fails():
    stale = copy.deepcopy(PACKAGE)
    stale["resources"].append({"name": "Excel", "format": "XLSX", "url": URL + "/file.xlsx", "description": "x"})
    assert compare(PUBLIC, URL, stale) == ["file.xlsx: removed (T-4597) and still listed"]


def test_a_surface_without_its_usage_text_or_with_the_wrong_format_fails():
    bare = copy.deepcopy(PACKAGE)
    for resource in bare["resources"]:
        if resource["url"].endswith("/odata/v4/"):
            resource["description"] = ""
            resource["format"] = "JSON"
    assert compare(PUBLIC, URL, bare) == ["odata: format 'JSON', not 'OData'", "odata: no usage description"]


@pytest.mark.parametrize("leak", ["?token=abc", "?apikey=abc"])
def test_a_resource_url_with_a_credential_fails(leak):
    leaky = copy.deepcopy(PACKAGE)
    leaky["resources"][0]["url"] += leak
    assert compare(PUBLIC, URL, leaky) == ["NGSI-LD API: carries a credential"]


def test_a_usage_text_with_a_real_token_fails_and_the_placeholder_passes():
    texts = copy.deepcopy(PACKAGE)
    texts["resources"][1]["description"] = "websocat -H \"Authorization: Bearer $TOKEN\" 'wss://x/ws'"
    assert compare(PUBLIC, URL, texts) == []
    texts["resources"][1]["description"] = "websocat -H \"Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.e30.c2ln\""
    assert compare(PUBLIC, URL, texts) == ["Live updates over WebSocket: carries a credential"]


def test_a_restricted_endpoint_as_a_public_dataset_fails():
    assert compare({**PUBLIC, "audience": "organization"}, URL, PACKAGE) == ["a restricted endpoint is a public dataset"]
    assert compare({**PUBLIC, "audience": "organization"}, URL, {**PACKAGE, "private": True}) == []


def test_no_dataset_fails():
    assert compare(PUBLIC, URL, None) == ["no dataset"]


class Recorded:
    """CKAN answering `package_show` from the recording, by dataset name."""

    bearer = None

    def __init__(self, packages):
        self.packages = packages

    def action(self, action, **params):
        assert action == "package_show"
        if params["id"] not in self.packages:
            raise RuntimeError("package_show answered 404")
        return self.packages[params["id"]]


def manifest(spec, name="hki-air-public"):
    return {"metadata": {"name": name, "namespace": "helsinki"}, "spec": spec, "status": {"url": URL + "/"}}


def test_an_internal_endpoint_with_a_dataset_fails_and_one_without_passes():
    internal = {"slug": "k7m2q4hkiair", "audience": "organization"}
    present = check_endpoint(Recorded({"hki-air-internal": PACKAGE}), manifest(internal, "hki-air-internal"), "", probe=False)
    assert not present.ok and "a dataset exists" in present.markdown()
    absent = check_endpoint(Recorded({}), manifest(internal, "hki-air-internal"), "", probe=False)
    assert absent.ok


def test_a_published_endpoint_is_compared_with_its_own_dataset():
    report = check_endpoint(Recorded({"hki-air": PACKAGE}), manifest(PUBLIC), "", probe=False)
    assert report.ok, report.markdown()
    report = check_endpoint(Recorded({"hki-air": without(PACKAGE, "/file.csv")}), manifest(PUBLIC), "", probe=False)
    assert not report.ok and "csv: no resource at /file.csv" in report.markdown()


def test_the_lag_percentiles_are_the_median_and_the_nearest_rank():
    assert percentiles([30.0]) == (30.0, 30.0)
    assert percentiles([10.0, 40.0, 20.0]) == (20.0, 40.0)
    assert percentiles([float(n) for n in range(1, 21)]) == (10.5, 19.0)
