"""Fixtures of the live authorization matrix (T-2797): the table, the Portal, and a token per role."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
# The Portal's own table; the sandbox keeps the Portal's clone beside this one.
DEFAULT_MATRIX = HERE.parent.parent.parent / "joinedcontext-portal" / "tests" / "authz_matrix.yaml"


def matrix_path() -> Path:
    return Path(os.environ.get("JC_AUTHZ_MATRIX") or DEFAULT_MATRIX)


def load_matrix() -> dict:
    path = matrix_path()
    if not path.is_file():
        return {"roles": [], "routes": []}
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="session")
def portal_url() -> str:
    value = os.environ.get("PORTAL_URL")
    if not value:
        pytest.skip("PORTAL_URL is not set — see tests/authz/README.md")
    return value.rstrip("/")
