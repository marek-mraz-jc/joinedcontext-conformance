"""Reading a CKAN instance the way a harvester does (T-0320, EP-62, EP-64, EP-65).

Only reads. The suite checks what a publication run left behind; it never creates, updates or
deletes anything, so pointing it at a production catalogue is safe and pointing it at a
throwaway one costs nothing. The API token is optional and only widens what CKAN answers: a
public dataset needs none, a private one does.
"""

from __future__ import annotations

import os
from typing import Any

import requests

TIMEOUT = 30


class CkanError(RuntimeError):
    """CKAN did not answer, or answered an error. Never carries the token."""


class Ckan:
    """One CKAN instance, addressed through its Action API."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = token

    @classmethod
    def from_environment(cls) -> "Ckan | None":
        """The instance `CKAN_URL` names, or None when the suite is not pointed at one."""
        url = os.environ.get("CKAN_URL")
        if not url:
            return None
        return cls(url, os.environ.get("CKAN_API_TOKEN"))

    def action(self, action: str, **params: Any) -> Any:
        answer = self.session.get(
            f"{self.url}/api/3/action/{action}", params=params, timeout=TIMEOUT
        )
        if answer.status_code == 404:
            raise CkanError(f"{action} answered 404 for {params}")
        if answer.status_code >= 400:
            raise CkanError(f"{action} answered {answer.status_code}")
        body = answer.json()
        if not body.get("success"):
            raise CkanError(f"{action} was refused: {body.get('error')}")
        return body["result"]

    def package(self, name: str) -> dict[str, Any]:
        """One dataset, as CKAN holds it."""
        return self.action("package_show", id=name)

    def rows(self, resource_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """A page of the DataStore table behind one resource (EP-65)."""
        return self.action("datastore_search", resource_id=resource_id, limit=limit)["records"]

    def reachable(self, url: str) -> int:
        """The status a citizen clicking a resource URL gets.

        `GET`, not `HEAD`: the gateway routes a download by method and an endpoint that
        answers `HEAD` with 405 while serving `GET` fine is not a broken resource.
        Redirects are followed, because an authenticating edge is allowed to send one.
        """
        try:
            answer = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        except requests.RequestException as error:
            raise CkanError(f"{url} could not be reached: {type(error).__name__}") from error
        answer.close()
        return answer.status_code


def datastore_resource(package: dict[str, Any]) -> dict[str, Any] | None:
    """The resource of a package that carries a DataStore table, if it has one (EP-65)."""
    for resource in package.get("resources") or []:
        if resource.get("datastore_active"):
            return resource
    return None
