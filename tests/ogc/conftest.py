"""Fixtures for the OGC API - Features suites (T-0058, EP-29…EP-39).

Everything comes from the environment: an endpoint URL that is wrong is better reported by its
own name than by a hardcoded default that quietly tests the wrong system.
"""

from __future__ import annotations

import os

import pytest
import requests


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set — see tests/ogc/README.md")
    return value


@pytest.fixture(scope="session")
def landing_url() -> str:
    return _require("OGC_LANDING_URL").rstrip("/")


@pytest.fixture(scope="session")
def http_session() -> requests.Session:
    session = requests.Session()
    token = os.environ.get("OGC_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/geo+json, application/json"
    with session:
        yield session


@pytest.fixture(scope="session")
def get(http_session: requests.Session, landing_url: str):
    def _get(path: str = "", **params):
        url = landing_url + ("" if not path or path.startswith("/") else "/") + path
        return http_session.get(url, params=params or None, timeout=60)

    return _get


@pytest.fixture(scope="session")
def collection(get) -> str:
    """The collection under test: OGC_COLLECTION, or the first one the endpoint advertises."""
    named = os.environ.get("OGC_COLLECTION")
    if named:
        return named
    response = get("/collections")
    assert response.status_code == 200, f"GET /collections answered {response.status_code}"
    collections = response.json().get("collections", [])
    if not collections:
        pytest.skip("the endpoint advertises no collection; set OGC_COLLECTION")
    return collections[0]["id"]


@pytest.fixture(scope="session")
def features(get, collection) -> list[dict]:
    """A page of features to derive queries from; without data the filter tests prove nothing."""
    response = get(f"/collections/{collection}/items", limit=10)
    assert response.status_code == 200, f"items answered {response.status_code}"
    items = response.json().get("features", [])
    if not items:
        pytest.skip(f"collection {collection} is empty, so a filter cannot be shown to narrow it")
    return items
