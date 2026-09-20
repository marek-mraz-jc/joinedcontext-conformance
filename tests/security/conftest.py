import os
import typing
import pytest
import requests


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.skip(f"environment variable {name} not set")
    return val


@pytest.fixture(scope="session")
def space_url() -> str:
    return _require_env("SPACE_URL").rstrip("/")


@pytest.fixture(scope="session")
def token_viewer() -> str:
    return _require_env("TOKEN_VIEWER")


@pytest.fixture(scope="session")
def token_steward() -> str:
    return _require_env("TOKEN_STEWARD")


@pytest.fixture(scope="session")
def granted_type() -> str:
    return os.getenv("GRANTED_TYPE", "AirQualityObserved")


@pytest.fixture(scope="session")
def forbidden_type() -> str:
    return os.getenv("FORBIDDEN_TYPE", "ServiceAccount")


@pytest.fixture(scope="session")
def granted_entity_id() -> str:
    return _require_env("GRANTED_ENTITY_ID")


@pytest.fixture(scope="session")
def forbidden_entity_id() -> str:
    return _require_env("FORBIDDEN_ENTITY_ID")


@pytest.fixture(scope="session")
def hidden_attr() -> str:
    return _require_env("HIDDEN_ATTR")


@pytest.fixture(scope="session")
def other_space_url() -> str:
    return _require_env("OTHER_SPACE_URL").rstrip("/")


@pytest.fixture(scope="session")
def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/ld+json"})
    return s


@pytest.fixture
def get(http_session: requests.Session, space_url: str) -> typing.Callable[..., requests.Response]:
    """GET on the space surface. Keyword arguments other than token/base_url/headers/params are
    NGSI-LD query parameters, so a test reads like the request it sends. Never raises on status:
    the tests assert the status themselves."""

    def _get(
        path: str,
        token: typing.Optional[str] = None,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict] = None,
        params: typing.Optional[dict] = None,
        **query,
    ) -> requests.Response:
        url = (base_url or space_url) + ("" if path.startswith("/") else "/") + path
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return http_session.get(url, headers=headers, params={**(params or {}), **query}, timeout=30)

    return _get


@pytest.fixture
def post_query(http_session: requests.Session, space_url: str) -> typing.Callable[..., requests.Response]:
    def _post_query(body: dict, token: typing.Optional[str] = None, base_url: typing.Optional[str] = None, **kwargs) -> requests.Response:
        url = (base_url or space_url) + "/entityOperations/query"
        headers = dict(kwargs.pop("headers", {}))
        headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return http_session.post(url, json=body, headers=headers, timeout=30, **kwargs)

    return _post_query


def entity_ids(response: requests.Response) -> list[str]:
    """Ids of an NGSI-LD array response. Deliberately strict: a body that is not a JSON array of
    entities is a failure of the thing under test, not an empty result set — swallowing it would
    make every "the forbidden id is not in the result" assertion pass on a broken gateway."""
    data = response.json()
    assert isinstance(data, list), f"expected a JSON array, got {type(data).__name__}: {data!r:.200}"
    ids = []
    for element in data:
        assert isinstance(element, dict) and "id" in element, f"array element without an id: {element!r:.200}"
        ids.append(element["id"])
    return ids


# -- the forge (T-1703, CC-41, PF-51) -------------------------------------------------------
#
# The configuration repository lives in a forge that is reachable by every signed-in person.
# `test_forge_side_door.py` plays the attack with a reader's own token, so the probes below
# need nothing an administrator holds; the two cases that read the branch protection rule and
# the one that plays a leaked platform credential ask for FORGE_PLATFORM_TOKEN and skip without
# it. Never point these at dev: the suite writes, and a throwaway forge is what it is for.


@pytest.fixture(scope="session")
def forge_url() -> str:
    return _require_env("FORGE_URL").rstrip("/")


@pytest.fixture(scope="session")
def forge_reader_token() -> str:
    """A token of a person who is signed in and reads the configuration repository — the least
    privilege anybody on the platform has, and the identity the whole attack is played from."""
    return _require_env("FORGE_READER_TOKEN")


@pytest.fixture(scope="session")
def forge_platform_token() -> typing.Optional[str]:
    """The Portal's own forge credential, played here as one that has leaked."""
    return os.getenv("FORGE_PLATFORM_TOKEN") or None


@pytest.fixture(scope="session")
def forge_org() -> str:
    return os.getenv("FORGE_ORG", "joinedcontext")


@pytest.fixture(scope="session")
def forge_repo() -> str:
    return os.getenv("FORGE_REPO", "configuration")


@pytest.fixture(scope="session")
def forge_branch() -> str:
    return os.getenv("FORGE_BRANCH", "main")


@pytest.fixture(scope="session")
def forge_other_org() -> str:
    """An organization of the same forge the reader is in no team of."""
    return os.getenv("FORGE_OTHER_ORG", "mesto-kosice")


@pytest.fixture(scope="session")
def forge_other_repo() -> str:
    return os.getenv("FORGE_OTHER_REPO", "configuration")


@pytest.fixture
def forge(http_session: requests.Session, forge_url: str, forge_reader_token: str):
    """A call on the forge's API as a chosen token. Never raises on status: every case here
    asserts the refusal itself, and a raised exception would hide which door opened."""

    def _call(
        method: str,
        path: str,
        token: typing.Optional[str] = None,
        json_body: typing.Optional[dict] = None,
    ) -> requests.Response:
        return http_session.request(
            method,
            forge_url + ("" if path.startswith("/") else "/") + path,
            headers={
                "Authorization": f"token {token or forge_reader_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=json_body,
            timeout=30,
            allow_redirects=False,
        )

    return _call
