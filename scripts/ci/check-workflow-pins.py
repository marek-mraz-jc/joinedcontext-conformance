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

A dispatch input, an event field or a branch name is text somebody else chose; written as
`${{ }}` inside a `run:` script it becomes shell code before the shell sees it, so it may only
reach a script through `env:` (T-3231).

Line-based on purpose: the runner has no YAML library installed by default, and both rules are
decidable from the text. A third rule, OPS-41: an image a workflow signs by digest also has a
CycloneDX SBOM attested to that digest. Run it from anywhere: `python3 scripts/ci/check-workflow-pins.py`.
"""

import re
import sys
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
USES = re.compile(r"^\s*(?:- )?uses:\s*(\S+)")
COMMIT = re.compile(r"@[0-9a-f]{40}$")
PUBLISHES = ("packages: write", "id-token: write")
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
# What a caller chooses: a dispatch's inputs, the triggering event's text, a branch name.
CHOSEN = re.compile(r"\$\{\{\s*(inputs\.|github\.event\.inputs\.|github\.event\.(issue|pull_request|comment|review|head_commit|commits|discussion|pages)|github\.head_ref)")


def chosen_in_scripts(lines):
    """Every line of a `run:` script that expands caller-chosen text with `${{ }}` (T-3231)."""
    inside, indent = False, 0
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if inside and stripped and len(line) - len(stripped) <= indent:
            inside = False
        if re.match(r"(- )?run:", stripped):
            inside, indent = True, len(line) - len(stripped)
            if CHOSEN.search(stripped.split("run:", 1)[1]):
                yield number
            continue
        if inside and CHOSEN.search(line.split("#", 1)[0]):
            yield number


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


def problems(path: Path):
    lines = path.read_text().splitlines()
    for number, image in unattested(lines):
        yield (
            f"{path.name}:{number}: {image} is signed and no CycloneDX SBOM is attested to it "
            "(`cosign attest --type cyclonedx`, OPS-41)"
        )
    for number in chosen_in_scripts(lines):
        yield (
            f"{path.name}:{number}: a run script expands caller-chosen text with ${{{{ }}}}; "
            "pass it through `env:` and read the variable (T-3231)"
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
    found = [problem for path in sorted(WORKFLOWS.glob("*.yml")) for problem in problems(path)]
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
