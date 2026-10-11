"""`$metadata` against the OASIS CSDL schemas (surfaces-conformance.md section 4).

The XML document validates against `edmx.xsd` (with `edm.xsd` through the OASIS catalog) by
`xmllint` from libxml2; the JSON document against `csdl.schema.json` (draft-07, `\\p{L}` patterns)
by `jsonschema-rs`, the Rust validator the image already pins, which reads Unicode classes that
Python's `re` does not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import jsonschema_rs


def test_metadata_xml_is_valid_csdl(odata_url, http, pins, tmp_path):
    resp = http.get(f"{odata_url}/$metadata", headers={"Accept": "application/xml"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["Content-Type"].startswith("application/xml"), resp.headers
    document = tmp_path / "metadata.xml"
    document.write_bytes(resp.content)
    xmllint = shutil.which("xmllint")
    assert xmllint, "xmllint is not installed (Debian package libxml2-utils)"
    result = subprocess.run(
        [xmllint, "--noout", "--nonet", "--catalogs", "--schema", str(pins / "schemas/edmx.xsd"), str(document)],
        env={**os.environ, "XML_CATALOG_FILES": str(pins / "catalog.xml")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_metadata_json_is_valid_csdl(odata_url, http, pins):
    resp = http.get(f"{odata_url}/$metadata?$format=json")
    assert resp.status_code == 200, resp.text
    schema = json.loads((pins / "schemas/csdl.schema.json").read_text(encoding="utf-8"))
    # The OData identifier patterns (`\\p{L}...{0,127}`) outgrow the regex engine's default
    # compiled size; the limit only bounds memory, not what a pattern means.
    validator = jsonschema_rs.validator_for(schema, pattern_options=jsonschema_rs.RegexOptions(size_limit=1 << 26))
    errors = [f"{e.instance_path}: {e.message}" for e in validator.iter_errors(resp.json())]
    assert not errors, "\n".join(errors)
