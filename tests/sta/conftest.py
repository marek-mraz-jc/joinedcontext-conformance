"""Fixtures for the OGC SensorThings API suite (T-0059, EP-12, EP-13, TS-08)."""

from __future__ import annotations

import os

import pytest
import requests


@pytest.fixture(scope="session")
def sta_url() -> str:
    value = os.environ.get("STA_URL")
    if not value:
        pytest.skip("STA_URL is not set — see tests/sta/README.md")
    return value.rstrip("/")


@pytest.fixture(scope="session")
def http_session() -> requests.Session:
    session = requests.Session()
    token = os.environ.get("STA_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/json"
    with session:
        yield session


@pytest.fixture(scope="session")
def get(http_session: requests.Session, sta_url: str):
    def _get(path: str = "", **params):
        url = sta_url + ("" if not path or path.startswith("/") else "/") + path
        # STA options carry a $ that requests must not re-encode into something else
        query = {key.replace("_", "$", 1) if key.startswith("_") else key: value for key, value in params.items()}
        return http_session.get(url, params=query or None, timeout=60)

    return _get


@pytest.fixture(scope="session")
def observations(get) -> list[dict]:
    response = get("Observations", _top=5)
    assert response.status_code == 200, f"Observations answered {response.status_code}"
    values = response.json().get("value", [])
    if not values:
        pytest.skip("the endpoint exposes no Observation, so a filter cannot be shown to narrow it")
    return values
