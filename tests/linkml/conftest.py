"""Fixtures of the LinkML surface suite (Antares T-3512, `surfaces-conformance.md` section 5).

The broker is the `full` build with the `linkml` surface, loaded by Antares `dev/views-rig.sh`:
view `all` shows every seeded Entity, view `parity-restricted` hides `NO2` (and every Entity
outside `scopeQ=/SK/BB`). Each view serves its narrowed schema under `/x/views/{view}/linkml`
(`odata-linkml-mapping.md` section 8); the documents are fetched once per view.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass

import pytest
import requests

# the attribute view `parity-restricted` hides and view `all` shows
HIDDEN = os.getenv("LINKML_HIDDEN_ATTRIBUTE", "NO2")
DOCUMENTS = ("model.linkml.yaml", "model.schema.json", "context.jsonld", "model.shacl.ttl")


def _env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.fail(f"{name} is not set, see tests/linkml/README.md")
    return val.rstrip("/")


@dataclass(frozen=True)
class Served:
    """One view's LinkML surface: its URL, its catalogue entry and its documents on disk."""

    url: str
    model: dict
    files: pathlib.Path

    def path(self, name: str) -> pathlib.Path:
        return self.files / name

    @property
    def types(self) -> list[str]:
        return self.model["types"]

    def entities(self, http: requests.Session) -> list[dict]:
        """Every Entity of the visible types, in keyValues, from the view's NGSI-LD root."""
        root = self.url.removesuffix("/linkml") + "/ngsi-ld/v1/entities"
        assert self.types, f"{self.url} shows no class: a check over its Entities would judge nothing"
        found = []
        for ty in self.types:
            resp = http.get(root, params={"type": ty, "format": "keyValues", "limit": 1000})
            assert resp.status_code == 200, f"{ty}: {resp.status_code} {resp.text}"
            assert resp.json(), f"no {ty} Entity in {root}: nothing to judge"
            found += resp.json()
        return found


def _served(http: requests.Session, url: str, into: pathlib.Path) -> Served:
    resp = http.get(f"{url}/schema/index.json")
    assert resp.status_code == 200, f"{url}: {resp.status_code} {resp.text}"
    models = resp.json()["models"]
    assert len(models) == 1, models
    model = models[0]
    into.mkdir(parents=True, exist_ok=True)
    for name in DOCUMENTS:
        doc = http.get(f"{url}/schema/v{model['version']}/{name}")
        assert doc.status_code == 200, f"{name}: {doc.status_code} {doc.text}"
        (into / name).write_bytes(doc.content)
    return Served(url, model, into)


@pytest.fixture(scope="session")
def http() -> requests.Session:
    return requests.Session()


@pytest.fixture(scope="session")
def view_all(http, tmp_path_factory) -> Served:
    return _served(http, _env("LINKML_URL"), tmp_path_factory.mktemp("all"))


@pytest.fixture(scope="session")
def view_restricted(http, tmp_path_factory) -> Served:
    return _served(http, _env("LINKML_RESTRICTED_URL"), tmp_path_factory.mktemp("restricted"))


@pytest.fixture(params=["all", "restricted"])
def served(request) -> Served:
    """Each view in turn."""
    return request.getfixturevalue(f"view_{request.param}")


def tool(*args: str) -> subprocess.CompletedProcess:
    """A LinkML command line tool of the pinned `linkml` package, its output captured."""
    if shutil.which(args[0]) is None:
        pytest.fail(f"{args[0]} is not on PATH: install requirements.txt")
    return subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
