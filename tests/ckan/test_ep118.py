"""EP-118: the verdicts of `ep118.py` (T-4324) on canned answers, so the fast lane proves each rule can go
red without a catalogue or a gateway."""

from __future__ import annotations

import json

import requests
from ep118 import ALWAYS, check, small, table

ROOT = "https://dev.joinedcontext.com/api/endpoint/abc/"
MODEL = {"JSON Schema": "model.schema.json", "JSON-LD": "context.jsonld", "LinkML": "model.linkml.yaml",
         "Markdown": "model.md", "OWL": "model.owl.ttl", "RDF": "model.rdf.ttl", "SHACL": "model.shacl.ttl"}


class Answer:
    def __init__(self, status, body=b"[]"):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def json(self):
        return json.loads(self.content)


def dataset(*extra, drop=(), rows=None):
    """A conforming dataset's resources, without the formats in `drop`, plus `extra`."""
    resources = [
        {"format": "NGSI-LD", "url": ROOT + "ngsi-ld/v1/entities?type=Supplier&q=isPartOf%3D%3D%22d%22"},
        {"format": "CSV", "url": ROOT + "file.csv?type=Supplier"},
        {"format": "MCP", "url": ROOT + "mcp"},
        {"format": "WebSocket", "name": "WebSocket (Supplier)", "url": "wss://dev.joinedcontext.com/api/endpoint/abc/ws"},
    ] + [{"format": label, "url": ROOT + "schema/v1/" + file} for label, file in MODEL.items()]
    return {"name": "bbsk-dodavatelia", "resources": [r for r in resources if r["format"] not in drop] + list(extra)}


def run(package, answers=None, ws=None, mcp=None, rows=None):
    asked, socket = [], []

    def get(url):
        asked.append(url)
        answer = (answers or {}).get(url.split("?")[0].removeprefix(ROOT))
        if isinstance(answer, Exception):
            raise answer
        if answer is None and "/ngsi-ld/" in url:
            return Answer(200, rows if rows is not None else [{"id": "urn:ngsi-ld:Supplier:1"}])
        return answer or Answer(200)

    verdict = check(package, get, lambda url, kind: socket.append(kind) or ws, lambda url: mcp)
    return verdict, asked, socket


def test_a_conforming_dataset_passes_and_every_link_is_read_small():
    verdict, asked, socket = run(dataset())
    assert verdict["ok"], verdict
    assert ROOT + "file.csv?type=Supplier" in asked, "a file is read as listed: its route takes no limit"
    assert ROOT + "ngsi-ld/v1/entities?type=Supplier&q=isPartOf%3D%3D%22d%22&limit=1" in asked
    assert ROOT + "schema/v1/model.schema.json" in asked, "a model artefact is read whole"
    assert socket == ["Supplier"]
    assert small(ROOT + "odata/v4/Supplier") == ROOT + "odata/v4/Supplier?$top=1"
    assert small(ROOT + "ogc/features/collections/Supplier/items") == ROOT + "ogc/features/collections/Supplier/items?limit=1"
    for root in ("ngsi-ld/v1/", "ogc/features/", "odata/v4/", "file.csv"):
        assert small(ROOT + root) == ROOT + root, root
    assert small("https://example.org/x.csv") == "https://example.org/x.csv"


def test_a_published_endpoint_s_root_links_are_read_as_roots_and_its_first_type_tells_location():
    """jcctl publishes an Endpoint's roots (`/ngsi-ld/v1/`, `https://…/ws`, `/ogc/features/`), not
    one type's query: the socket is still opened as a socket and the location is read off the
    first type's entities."""
    package = {"name": "helsinki-bikes", "resources": [
        {"format": "NGSI-LD", "url": ROOT + "ngsi-ld/v1/"},
        {"format": "WebSocket", "name": "Live updates over WebSocket", "url": ROOT + "ws"},
        {"format": "OGCFEAT", "url": ROOT + "ogc/features/"}]}
    asked, socket = [], []

    def get(url):
        asked.append(url)
        return Answer(200, [{"id": "x", "location": {}}] if "/entities?" in url else {})

    verdict = check(package, get, lambda url, kind: socket.append((url, kind)), lambda url: None,
                    first_type=lambda root: "BikeHireDockingStation" if root == ROOT.rstrip("/") else None)
    assert socket == [(ROOT + "ws", "BikeHireDockingStation")], "an https socket link is not a GET"
    assert ROOT + "ngsi-ld/v1/entities?type=BikeHireDockingStation&limit=1" in asked and ROOT + "ogc/features/" in asked
    assert verdict["located"] and verdict["failed"] == []


def test_a_missing_mandatory_distribution_fails_and_is_named():
    for fmt in ALWAYS:
        verdict, _, _ = run(dataset(drop={fmt}))
        assert not verdict["ok"] and verdict["missing"] == [fmt], (fmt, verdict)


def test_located_rows_need_ogc_features():
    located = [{"id": "urn:ngsi-ld:Supplier:1", "location": {"type": "GeoProperty"}}]
    verdict, _, _ = run(dataset(), rows=located)
    assert verdict["located"] and verdict["missing"] == ["OGCFEAT"]
    full = dataset({"format": "OGCFEAT", "url": ROOT + "ogc/features/collections/Supplier/items"})
    assert run(full, rows=located)[0]["ok"]


def test_zip_esri_rest_a_zip_url_and_a_removed_endpoint_file_are_banned():
    for extra in ({"format": "ZIP", "url": ROOT + "file.zip"}, {"format": "Esri REST", "url": "https://x/FeatureServer"},
                  {"format": "", "url": "https://dtm.example/opendata/ulice.zip"},
                  {"format": "XLSX", "url": ROOT + "file.xlsx"}, {"format": "GeoJSON", "url": ROOT + "file.geojson"},
                  {"format": "JSON", "url": ROOT + "file.json?type=Supplier"}):
        verdict, _, _ = run(dataset(extra))
        assert not verdict["ok"] and verdict["banned"], (extra, verdict)


def test_a_link_that_does_not_answer_200_fails_whatever_its_cause():
    for answer, shown in ((Answer(403, {"title": "Access Denied by Policy"}), "CSV 403"),
                          (Answer(413), "CSV 413"), (requests.ConnectionError(), "CSV ConnectionError")):
        verdict, _, _ = run(dataset(), answers={"file.csv": answer})
        assert not verdict["ok"] and verdict["failed"] == [shown], verdict
    verdict, _, _ = run(dataset(), ws="x-subscribe answered 403", mcp="initialize answered 500")
    assert verdict["failed"] == ["MCP initialize answered 500", "WebSocket x-subscribe answered 403"]


def test_a_probe_that_raises_fails_its_link_and_the_sweep_goes_on():
    def timeout(url):
        raise requests.ReadTimeout()

    verdict = check(dataset(), lambda url: Answer(200, [{"id": "x"}]), lambda url, kind: None, timeout)
    assert verdict["failed"] == ["MCP ReadTimeout"] and not verdict["ok"]


def test_the_table_has_one_row_per_dataset_and_a_tally():
    good, _, _ = run(dataset())
    bad, _, _ = run(dict(dataset(drop={"MCP"}), name="bb-zmluvy-bb"))
    text = table([good, bad])
    assert "| bbsk-dodavatelia | no | yes |" in text and "| bb-zmluvy-bb |" in text
    assert text.splitlines()[-1] == "datasets: 2, OK 1, FAIL 1"
    assert "missing" in text.splitlines()[3] and text.splitlines()[3].endswith("FAIL |")
