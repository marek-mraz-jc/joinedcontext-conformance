#!/usr/bin/env python3
"""Every action a lane runs is a commit, and only a job that publishes may publish (T-0851).

Two rules keep a pull request from a fork out of the repository's credentials (T-1714):
`pull_request_target` runs a fork's change with this repository's secrets and a write token, so
no workflow uses it; and every workflow names its `permissions:` at the top, so a job nobody
scoped gets read access instead of the repository default.

A tag is a reference somebody else owns: whoever moves one runs code in these workflows, and
`image.yml` carries `packages: write` and `id-token: write` — the two permissions that push an
image under the project's name and mint the identity that signs it. A workflow-level grant hands
both to every job in the file, including one added later for something unrelated.

Line-based on purpose: the runner has no YAML library installed by default, and both rules are
decidable from the text. A third rule, OPS-41: an image a workflow signs by digest also has a
CycloneDX SBOM attested to that digest. A fourth: a Dockerfile that runs `apt-get upgrade` takes
an `ARG APT_REFRESH` before it and every cached build passes a fresh value, or the build cache
replays a days-old upgrade layer and trivy fails on a fix Debian already ships (T-5372, libexpat1
deb12u5 missing from a CACHED layer). Run it from anywhere: `python3 scripts/ci/check-workflow-pins.py`.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
USES = re.compile(r"^\s*(?:- )?uses:\s*(\S+)")
COMMIT = re.compile(r"@[0-9a-f]{40}$")
PUBLISHES = ("packages: write", "id-token: write")
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def pushed_image(line: str):
    """The `image@digest` a cosign line names, with its `${{ }}` written without spaces."""
    tight = EXPRESSION.sub(lambda m: "${{" + m.group(1).replace(" ", "") + "}}", line)
    refs = [token.strip("\"'") for token in tight.split() if "@${{" in token and "outputs.digest" in token]
    return refs[-1] if refs else None


def unattested(lines):
    """OPS-41: every image a workflow pushes and signs by digest carries a CycloneDX SBOM attested
    to that same digest, so a signed image never ships without its bill of materials."""
    signed, attested = {}, set()
    for number, line in enumerate(lines, 1):
        code = line.split("#", 1)[0]
        if "cosign sign " in code and (image := pushed_image(code)):
            signed.setdefault(image, number)
        if "cosign attest " in code and "--type cyclonedx" in code and (image := pushed_image(code)):
            attested.add(image)
    return [(number, image) for image, number in signed.items() if image not in attested]


def unrefreshed_upgrade(dockerfile):
    """True when the Dockerfile runs `apt-get upgrade` with no `ARG APT_REFRESH` declared before it."""
    upgrade = next((n for n, line in enumerate(dockerfile) if "apt-get upgrade" in line), None)
    return upgrade is not None and not any(
        line.split("#", 1)[0].strip() == "ARG APT_REFRESH" for line in dockerfile[:upgrade]
    )


def stale_builds(lines):
    """Line numbers of build-push-action steps that read the build cache (`cache-from`) and pass
    no `APT_REFRESH=` build argument, so they replay whatever upgrade layer the cache holds."""
    stale = []
    for number, line in enumerate(lines, 1):
        if not (used := USES.match(line)) or not used.group(1).startswith("docker/build-push-action@"):
            continue
        indent = len(line) - len(line.lstrip(" -"))
        step = []
        for follow in lines[number:]:
            if follow.strip() and (len(follow) - len(follow.lstrip(" -")) < indent or follow.lstrip().startswith("- ")):
                break
            step.append(follow.split("#", 1)[0])
        if any("cache-from:" in s for s in step) and not any("APT_REFRESH=" in s for s in step):
            stale.append(number)
    return stale


def problems(path: Path, upgrades: bool):
    lines = path.read_text().splitlines()
    for number in stale_builds(lines) if upgrades else []:
        yield (
            f"{path.name}:{number}: this cached build replays an old `apt-get upgrade` layer; pass "
            "`build-args: APT_REFRESH=<today>` (T-5372)"
        )
    for number, image in unattested(lines):
        yield (
            f"{path.name}:{number}: {image} is signed and no CycloneDX SBOM is attested to it "
            "(`cosign attest --type cyclonedx`, OPS-41)"
        )
    if not any(line.startswith("permissions:") for line in lines):
        yield f"{path.name}: no top-level `permissions:`; every job would get the repository default"
    for number, line in enumerate(lines, 1):
        if "pull_request_target" in line.split("#", 1)[0]:
            yield f"{path.name}:{number}: pull_request_target runs a fork's change with this repository's secrets"
        used = USES.match(line)
        # A local `./…` call is this repository at the commit already checked out; there is no
        # third-party tag in it to move.
        if used and not used.group(1).startswith("./") and not COMMIT.search(used.group(1)):
            yield f"{path.name}:{number}: {used.group(1)} is a tag, not a commit"
        if not line.startswith("permissions:"):
            continue
        block = [line]
        for follow in lines[number:]:
            if follow.startswith((" ", "\t", "#")) or not follow.strip():
                block.append(follow)
            else:
                break
        for grant in PUBLISHES:
            if grant in "\n".join(block):
                yield (
                    f"{path.name}:{number}: `{grant}` is granted to every job in the file; "
                    "grant it on the job that publishes"
                )


def main() -> int:
    dockerfile = ROOT / "Dockerfile"
    lines = dockerfile.read_text().splitlines() if dockerfile.exists() else []
    upgrades = any("apt-get upgrade" in line for line in lines)
    found = [problem for path in sorted(WORKFLOWS.glob("*.yml")) for problem in problems(path, upgrades)]
    if unrefreshed_upgrade(lines):
        found.append("Dockerfile: `apt-get upgrade` with no `ARG APT_REFRESH` before it; the build cache keeps an old upgrade (T-5372)")
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
