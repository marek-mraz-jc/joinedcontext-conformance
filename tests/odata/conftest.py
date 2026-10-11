"""Fixtures of the OData conformance suite (T-3510, Antares `surfaces-conformance.md` section 4).

The broker is the `odata` build loaded by Antares `dev/views-rig.sh`: view `all` sees every seeded
Entity, view `parity-restricted` a fixed allowlist under `scopeQ=/SK/BB`. The OASIS files are
fetched at their pinned commits and checked against their SHA-256 before use, never vendored
(OASIS IPR policy).
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
import urllib.request

import pytest
import requests

ABNF = "https://raw.githubusercontent.com/oasis-tcs/odata-abnf/a31c8db229a0d8e957bee86512b01efe7ce74173/"
CSDL = "https://raw.githubusercontent.com/oasis-tcs/odata-csdl-schemas/e6b66a96ec5f74edb0c0e192978a39f146f70d76/"
# path below the pins directory: (URL, SHA-256)
PINS = {
    "odata-abnf-testcases.yaml": (
        ABNF + "abnf/odata-abnf-testcases.yaml",
        "feded192570e4c7e64c60b47d9631ae20da7cd1c974d54ae58111cd84d12c921",
    ),
    "catalog.xml": (CSDL + "catalog.xml", "30d8a483f0031925475e11686b3becf034ad3782369f3e314bfa919e19badb62"),
    "schemas/edmx.xsd": (CSDL + "schemas/edmx.xsd", "bb8dd3e090955cc7653e361eec8f48b02c095979992839ebe878a3e49dcbd316"),
    "schemas/edm.xsd": (CSDL + "schemas/edm.xsd", "a2e01a415137b5e84d442f8f35cb5d2e054b9fe5e6d18dab18c76bab8fea8d86"),
    "schemas/csdl.schema.json": (
        CSDL + "schemas/csdl.schema.json",
        "c211eff8a91be39f594b1eb84760306b730cda9a82ec204b2f8186e5018ffb80",
    ),
}
ENTITY_SET = os.getenv("ODATA_ENTITY_SET", "AirQualityObserved")
HEADERS = {"OData-MaxVersion": "4.01"}


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.skip(f"{name} is not set, see tests/odata/README.md")
    return val.rstrip("/")


@pytest.fixture(scope="session")
def odata_url() -> str:
    """Service root of view `all`."""
    return _require_env("ODATA_URL")


@pytest.fixture(scope="session")
def restricted_url() -> str:
    """Service root of view `parity-restricted`."""
    return _require_env("ODATA_RESTRICTED_URL")


def ngsild_of(service_root: str) -> str:
    """The NGSI-LD door of the same view: `/x/views/{view}/ngsi-ld/v1`."""
    return service_root.removesuffix("/odata/v4") + "/ngsi-ld/v1"


@pytest.fixture(scope="session")
def http() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


@pytest.fixture(scope="session")
def pins() -> pathlib.Path:
    """The pinned OASIS files, in `ODATA_PINS_DIR` (kept between runs) or a fresh directory."""
    root = pathlib.Path(os.getenv("ODATA_PINS_DIR") or tempfile.mkdtemp(prefix="odata-pins-"))
    for path, (url, sha256) in PINS.items():
        target = root / path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=30) as resp:
                target.write_bytes(resp.read())
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != sha256:
            pytest.fail(f"{path}: SHA-256 {digest}, pinned {sha256}")
    return root


def is_odata_error(resp: requests.Response) -> bool:
    """The OData JSON error object (JSON Format 21.1): `{"error": {"code", "message"}}`."""
    try:
        body = resp.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and isinstance(error.get("code"), str) and isinstance(error.get("message"), str)


def count(http: requests.Session, service_root: str) -> int:
    resp = http.get(f"{service_root}/{ENTITY_SET}/$count")
    assert resp.status_code == 200, resp.text
    return int(resp.text)
