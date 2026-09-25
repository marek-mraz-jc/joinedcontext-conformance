"""The catalogue description of a project's endpoints, checked live (T-2790…T-2793, EP-78…EP-80).

`dcat.py` proves a record is DCAT-AP. This proves it says what a catalogue reader needs from a
city: who publishes it and how to reach their open-data desk, under which licence, about what,
where, how often, and in the city's own languages beside English; and that the ODRL offer the
record carries is the one its licence and audience make (EP-79).

    python3 tests/ckan/catalog.py helsinki --languages fi,sv,en

The endpoints of the project are read from the forge's bootstrap seed (`kubectl`, read only),
or from `CATALOG_SLUGS` (comma-separated) when there is no cluster at hand. Every record is
read anonymously, the way a harvester reads it, as JSON-LD and as Turtle; both go through the
pinned SEMIC DCAT-AP 3.0 shapes and must be one graph. The table it prints is the list the
task records: endpoint, licence, themes, verdict. Exit 1 when any endpoint fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import rdflib
from rdflib.compare import isomorphic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dcat import graph, shape_violations  # noqa: E402

#: The SEMIC release the platform validates against in its fast lane (EP-78), by digest: a
#: shapes file that is not this one proves conformance to something else.
SEMIC_SHAPES_SHA256 = "92f76609d78d257123e75bc6b7155df5cc0a63f14c29fbc12b0ac95c56af2059"
SEMIC_SHAPES_IN_PLATFORM = (
    "joinedcontext-platform/crates/context-gateway/tests/fixtures/dcat-ap/dcat-ap-3.0.0-SHACL.ttl"
)

LICENCE_TABLE = "http://publications.europa.eu/resource/authority/licence/"
ATTRIBUTE = "odrl:attribute"
SHARE_ALIKE = "http://creativecommons.org/ns#ShareAlike"

#: What each licence of the EU table the platform knows asks of a user (EP-79).
DUTIES: dict[str, frozenset[str]] = {
    "CC0": frozenset(),
    "ODC_PDDL": frozenset(),
    "CC_BY_4_0": frozenset({ATTRIBUTE}),
    "ODC_BY": frozenset({ATTRIBUTE}),
    "CC_BYSA_4_0": frozenset({ATTRIBUTE, SHARE_ALIKE}),
    "ODC_ODBL": frozenset({ATTRIBUTE, SHARE_ALIKE}),
}

#: What a city's record must carry beyond the DCAT-AP mandatory core (EP-78).
CATALOGUE_TERMS = (
    "dct:publisher",
    "dcat:contactPoint",
    "dct:license",
    "dcat:theme",
    "dct:spatial",
    "dct:accrualPeriodicity",
    "dcat:keyword",
)

PUBLIC = "http://publications.europa.eu/resource/authority/access-right/PUBLIC"


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def licence_code(record: dict[str, Any]) -> str | None:
    """The EU licence table code the record names, or None."""
    for value in _list(record.get("dct:license")):
        iri = value.get("@id") if isinstance(value, dict) else value
        if isinstance(iri, str) and iri.startswith(LICENCE_TABLE):
            return iri[len(LICENCE_TABLE) :]
    return None


def themes(record: dict[str, Any]) -> list[str]:
    """The data-theme codes the record names, in its order."""
    return [
        str(value.get("@id") if isinstance(value, dict) else value).rsplit("/", 1)[-1]
        for value in _list(record.get("dcat:theme"))
    ]


def uncatalogued(record: dict[str, Any], languages: list[str]) -> list[str]:
    """What the record leaves out of the catalogue description, each named (EP-78, EP-80)."""
    missing = [term for term in CATALOGUE_TERMS if not _list(record.get(term))]
    code = licence_code(record)
    if record.get("dct:license") and code not in DUTIES:
        missing.append(f"dct:license: {record.get('dct:license')!r} is not a licence whose duties are known")
    said = {
        keyword.get("@language")
        for keyword in _list(record.get("dcat:keyword"))
        if isinstance(keyword, dict)
    }
    missing.extend(f"dcat:keyword in '{language}'" for language in languages if language not in said)
    for contact in _list(record.get("dcat:contactPoint")):
        email = contact.get("vcard:hasEmail") if isinstance(contact, dict) else None
        if not (isinstance(email, str) and email.startswith("mailto:")):
            missing.append("dcat:contactPoint.vcard:hasEmail as a mailto: address")
    for distribution in _list(record.get("dcat:distribution")):
        if isinstance(distribution, dict) and code and licence_code(distribution) != code:
            missing.append(f"dct:license {code} on distribution {distribution.get('@id')}")
    return missing


def offer(record: dict[str, Any]) -> dict[str, Any] | None:
    """The `odrl:Offer` among the record's policies, or None."""
    for policy in _list(record.get("odrl:hasPolicy")):
        if isinstance(policy, dict) and "odrl:Offer" in _list(policy.get("@type")):
            return policy
    return None


def offer_mismatches(record: dict[str, Any]) -> list[str]:
    """How the record's offer departs from what its licence and audience make (EP-79).

    A licensed record offers `odrl:use` of itself, with exactly the duties of its licence. A
    public one sets no condition on who; any other constrains the recipient to the publishing
    organization and names the audience. An unlicensed record makes no offer at all.
    """
    code = licence_code(record)
    made = offer(record)
    if code is None:
        return ["an offer without a licence"] if made else []
    if made is None:
        return [f"licence {code} but no odrl:Offer in odrl:hasPolicy"]
    problems: list[str] = []
    permissions = [item for item in _list(made.get("odrl:permission")) if isinstance(item, dict)]
    if len(permissions) != 1:
        return [f"the offer holds {len(permissions)} permissions, not one"]
    permission = permissions[0]
    if permission.get("odrl:action") != "odrl:use":
        problems.append(f"the offer permits {permission.get('odrl:action')!r}, not odrl:use")
    if permission.get("odrl:target") != record.get("@id"):
        problems.append(f"the offer targets {permission.get('odrl:target')!r}, not the dataset")
    duties = {
        duty.get("odrl:action") for duty in _list(permission.get("odrl:duty")) if isinstance(duty, dict)
    }
    if duties != DUTIES.get(code, frozenset()):
        problems.append(f"licence {code} asks {sorted(DUTIES.get(code, ()))}, the offer asks {sorted(duties)}")
    constraints = [item for item in _list(permission.get("odrl:constraint")) if isinstance(item, dict)]
    public = PUBLIC in [
        value.get("@id") if isinstance(value, dict) else value for value in _list(record.get("dct:accessRights"))
    ]
    if public and constraints:
        problems.append("a public record's offer constrains who may use it")
    if not public:
        operands = {item.get("odrl:leftOperand") for item in constraints}
        if "odrl:recipient" not in operands:
            problems.append("a restricted record's offer does not constrain the recipient")
        if "ngsi-ld:audience" not in operands:
            problems.append("a restricted record's offer does not name its audience")
    return problems


def check(jsonld: dict[str, Any], turtle: str, shapes: str, languages: list[str]) -> list[str]:
    """Every finding for one endpoint's record, as JSON-LD and as Turtle."""
    findings = [f"SEMIC: {line}" for line in shape_violations(jsonld, shapes)]
    try:
        other = rdflib.Graph().parse(data=turtle, format="turtle")
        if not isomorphic(graph(jsonld), other):
            findings.append("the Turtle and the JSON-LD are not one graph")
    except Exception as error:  # noqa: BLE001 - a record that will not parse is the finding
        findings.append(f"the Turtle does not parse: {error}")
    findings.extend(uncatalogued(jsonld, languages))
    findings.extend(offer_mismatches(jsonld))
    return findings


def seeded_endpoints(project: str, namespace: str) -> dict[str, str]:
    """Endpoint name → slug for every endpoint of the project in the forge's bootstrap seed."""
    raw = subprocess.run(
        ["kubectl", "get", "configmap", "gitea-bootstrap-seed", "-n", namespace, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: dict[str, str] = {}
    prefix = f"projects__{project}__spaces__"
    for key, body in json.loads(raw).get("data", {}).items():
        if key.startswith(prefix) and "__endpoints__" in key and key.endswith(".yaml"):
            name = key.rsplit("__", 1)[-1].removesuffix(".yaml")
            slug = next(
                (line.split(":", 1)[1].strip() for line in body.splitlines() if line.strip().startswith("slug:")),
                None,
            )
            if slug:
                found[name] = slug
    return found


def fetch(url: str, accept: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https base only
        return response.read().decode("utf-8")


def shapes_file() -> Path:
    """The pinned SEMIC shapes: `DCAT_AP_SHACL`, else the platform clone beside this one."""
    here = Path(__file__).resolve().parents[3]
    path = Path(os.environ.get("DCAT_AP_SHACL") or here / SEMIC_SHAPES_IN_PLATFORM)
    if not path.is_file():
        raise SystemExit(f"no SEMIC shapes at {path}: set DCAT_AP_SHACL to the pinned file ({SEMIC_SHAPES_IN_PLATFORM})")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SEMIC_SHAPES_SHA256:
        raise SystemExit(f"{path} is not the pinned SEMIC DCAT-AP 3.0.0 release (sha256 {digest})")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project")
    parser.add_argument("--languages", default="en", help="keyword languages every record must carry")
    parser.add_argument("--base", default=os.environ.get("JC_DEV_BASE", "https://dev.joinedcontext.com"))
    parser.add_argument("--namespace", default=os.environ.get("JC_DEV_NS", "dev"))
    args = parser.parse_args(argv)
    if not args.base.startswith("https://"):
        raise SystemExit(f"--base must be an https URL, got {args.base!r}")
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    shapes = shapes_file().read_text(encoding="utf-8")

    listed = os.environ.get("CATALOG_SLUGS")
    endpoints = (
        {slug: slug for slug in (item.strip() for item in listed.split(",")) if slug}
        if listed
        else seeded_endpoints(args.project, args.namespace)
    )
    if not endpoints:
        print(f"{args.project}: no endpoint in the seed, nothing was checked", file=sys.stderr)
        return 1

    failed = 0
    print(f"| Endpoint | Licence | Themes | Verdict |\n|---|---|---|---|")
    for name, slug in sorted(endpoints.items()):
        url = f"{args.base.rstrip('/')}/api/endpoint/{slug}/"
        try:
            jsonld = json.loads(fetch(url, "application/ld+json"))
            findings = check(jsonld, fetch(url, "text/turtle"), shapes, languages)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
            jsonld, findings = {}, [f"the record could not be read: {error}"]
        failed += bool(findings)
        verdict = "passes" if not findings else "fails: " + "; ".join(findings)
        print(f"| {name} | {licence_code(jsonld) or '-'} | {', '.join(themes(jsonld)) or '-'} | {verdict} |")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
