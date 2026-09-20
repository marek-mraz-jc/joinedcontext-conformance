#!/usr/bin/env python3
"""The compliance run: every requirement with the named tests that prove it (T-1725, TS-19, OPS-27).

`docs/Requirements/traceability.md` maps a requirement *family* to a suite. This maps one
requirement to the named tests that prove it, and runs them as one thing. The index is the
citations that already exist in the repositories: a test that proves a requirement names its id
in its doc comment, its docstring, its title or its `[Tags]` line.

    compliance.py index  [--repo name=path ...] [--docs path] [--index compliance/index.json]
                         [--matrix docs/Requirements/compliance-matrix.md]
    compliance.py check  [--index …] [--baseline compliance/baseline.json] [--scan]
    compliance.py report --junit <dir-or-file> … [--index …] [--out-html …] [--out-json …]

The checker is checked by `tests/test_compliance_script.py`.

`index` walks the repositories and writes the index and the committed matrix page. `check` and
`report` walk them again rather than trust a file on disk, which takes under a second. `check`
fails on an unproven security requirement, a citation of an id that does not exist, a skipped test
with no task id beside the skip, and on the count of uncited requirements rising above the
committed baseline. `report` joins the index with
the JUnit output of the lanes and renders a requirement green only when every test citing it
passed in that input; no number in the report is typed, every one is read from the JUnit files.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

SKIP_DIRECTORIES = {
    ".git", ".github", "node_modules", "target", "dist", "build", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", "vendor", ".next", "coverage",
    "test-results", "playwright-report", ".attic",
}
# The five repositories of the platform, as they sit beside each other in the working tree.
DEFAULT_REPOS = (
    "joinedcontext-platform", "joinedcontext-portal", "joinedcontext-conformance",
    "joinedcontext-deployment", "docs",
)

# Which lane runs a test, by repository and path prefix; first match wins. The lanes are the ones
# CLAUDE.md names: the fast `ci` merge gate, the hourly `ci-full`, the conformance workflows, and
# the suites that only run against a live cluster.
LANES: tuple[tuple[str, str, str], ...] = (
    ("joinedcontext-platform", "crates/*/src/", "fast ci"),
    ("joinedcontext-platform", "crates/*/tests/", "ci-full"),
    ("joinedcontext-platform", "tools/", "fast ci"),
    ("joinedcontext-portal", "ui/tests/", "fast ci"),
    ("joinedcontext-portal", "ui/src/", "fast ci"),
    ("joinedcontext-portal", "ui/e2e/live/", "live on dev"),
    ("joinedcontext-portal", "ui/e2e/", "ci-full"),
    ("joinedcontext-portal", "apps/", "fast ci"),
    ("joinedcontext-portal", "sdk/", "fast ci"),
    ("joinedcontext-portal", "src/", "fast ci"),
    ("joinedcontext-portal", "tests/", "ci-full"),
    ("joinedcontext-conformance", "tests/etsi-ttf/", "conformance"),
    ("joinedcontext-conformance", "tests/etsi/", "live on dev"),
    ("joinedcontext-conformance", "tests/k6/", "live on dev"),
    ("joinedcontext-conformance", "tests/chaos/", "live on dev"),
    ("joinedcontext-conformance", "tests/", "conformance"),
    ("joinedcontext-conformance", "e2e/", "conformance"),
    ("joinedcontext-deployment", "", "fast ci"),
    ("docs", "", "fast ci"),
)

RUST_TEST_ATTRIBUTES = ("#[test]", "#[tokio::test", "#[rstest", "#[test_case", "#[proptest",
                        "#[actix_rt::test", "#[async_std::test", "#[googletest::test")
RUST_FN = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z0-9_]+)")
TS_DESCRIBE = re.compile(r"""^(\s*)describe(?:\.\w+(?:\([^)]*\))?)?\s*\(\s*(['"`])(.*?)\2""")
TS_TEST = re.compile(r"""^(\s*)(it|test)((?:\.\w+(?:\([^)]*\))?)*)\s*\(\s*(['"`])(.*?)\4""")
PY_DEF = re.compile(r"^(\s*)def\s+(test_[A-Za-z0-9_]*)\s*\(")
PY_DECORATOR = re.compile(r"^\s*@")
TRIPLE_QUOTES = ('"' * 3, "'" * 3)
ROBOT_SECTION = re.compile(r"^\*+\s*([A-Za-z ]+?)\s*\*+\s*$")
# A skip is allowed when a task id stands beside it; without one nothing brings the test back.
TASK_ID = re.compile(r"\bT-\d{3,5}\b")


@dataclass(frozen=True)
class Requirement:
    id: str
    family: str
    tags: tuple[str, ...]
    statement: str
    source: str
    line: int


@dataclass
class TestCase:
    repo: str
    path: str
    line: int
    name: str
    kind: str
    lane: str
    requirements: list[str] = field(default_factory=list)
    # "" runs, "conditional" is skipped only where the environment cannot run it, "unconditional"
    # is switched off. Only the last one hides a regression, so only it is a compliance failure.
    skip: str = ""
    skip_note: str = ""

    @property
    def reference(self) -> str:
        return f"{self.repo}/{self.path}::{self.name}"


def lane_for(repo: str, path: str) -> str:
    for lane_repo, prefix, lane in LANES:
        if lane_repo != repo:
            continue
        if "*" in prefix:
            head, _, tail = prefix.partition("*")
            if path.startswith(head) and tail in path[len(head):]:
                return lane
        elif path.startswith(prefix):
            return lane
    return "unknown"


# `- **PF-50** [S] — statement.` (docs/STYLE.md) and the legacy paragraph form of the R and I
# families, `**R45, Conditional writes …** statement.`
REQUIREMENT_BULLET = re.compile(
    r"^- \*\*([A-Za-z][A-Za-z0-9]*?-?\d{1,3}(?:-[A-Za-z]+\d{1,3})?)(?:[,;:. ]\s*[^*]*)?\*\*[,:]?\s*"
    r"((?:\[[A-Za-z]\]\s*)*)(?:[—–-]\s*)?(.*)$"
)
REQUIREMENT_PARAGRAPH = re.compile(r"^\*\*([A-Za-z][A-Za-z0-9]*?-?\d{1,3}),\s*(.*)$")
ID_PARTS = re.compile(r"^([A-Za-z]+)-?(\d{1,3})$")
# The two-level ids of the OASC families: `MIM0-R1`, `MIM7-R12`. The chapter number belongs to
# the prefix, so `MIM0-R1` and `MIM7-R1` are two requirements and not one read twice (T-2142).
TWO_LEVEL_PARTS = re.compile(r"^([A-Za-z]+\d{1,2}-[A-Za-z]+)-?(\d{1,3})$")


def split_id(identifier: str) -> tuple[str, int] | None:
    match = ID_PARTS.match(identifier) or TWO_LEVEL_PARTS.match(identifier)
    return (match.group(1).upper(), int(match.group(2))) if match else None


def family_of(identifier: str) -> str:
    """The family a requirement is counted under: `MIM0-R1` belongs to MIM, not to `MIM0-R`."""
    head = identifier.split("-")[0]
    return re.sub(r"\d+$", "", head).upper() or head.upper()


def read_requirements(docs: Path) -> list[Requirement]:
    """Every requirement defined in `docs/Requirements/*.md`, in file order.

    Two spellings are in use: the bullet `docs/STYLE.md` prescribes, and the paragraph form the
    legacy R and I families keep, whose bold title may wrap over several lines.
    """
    found: dict[str, Requirement] = {}
    for page in sorted((docs / "Requirements").glob("*.md")):
        lines = page.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            bullet = REQUIREMENT_BULLET.match(line)
            paragraph = None if bullet else REQUIREMENT_PARAGRAPH.match(line)
            if bullet:
                identifier, tag_text, statement = bullet.group(1), bullet.group(2), bullet.group(3)
            elif paragraph:
                identifier, tag_text = paragraph.group(1), ""
                statement = paragraph.group(2)
                index = number
                while "**" not in statement and index < len(lines):
                    statement = f"{statement} {lines[index].strip()}"
                    index += 1
                statement = statement.replace("**", " ").strip()
            else:
                continue
            parts = split_id(identifier)
            if parts is None or identifier in found:
                continue
            found[identifier] = Requirement(
                id=identifier,
                family=family_of(identifier),
                tags=tuple(re.findall(r"\[([A-Za-z])\]", tag_text)),
                statement=" ".join(statement.split()),
                source=f"Requirements/{page.name}",
                line=number,
            )
    return list(found.values())


def citation_pattern(prefixes: set[str]) -> re.Pattern[str]:
    """Only prefixes that actually define a requirement are citations; `v1` and `sha256` are not.

    Neither `\\b` nor a plain boundary works here: a pytest name writes the id as
    `test_gw10_query_…`, where `_` is a word character, so the boundary is spelled out.

    A two-level prefix (`MIM0-R`) is one alternative like any other, and the longest match wins,
    so `MIM0-R1` is read as MIM0-R1 rather than as MIM 0.
    """
    alternatives = "|".join(re.escape(prefix) for prefix in sorted(prefixes, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])({alternatives})-?(\d{{1,3}})(?![A-Za-z0-9])", re.IGNORECASE)


def cited_ids(text: str, pattern: re.Pattern[str], canonical: dict[tuple[str, int], str]) -> list[str]:
    """Ids named in `text`, spelled as `docs/Requirements` spells them; unknown ones kept as read."""
    seen: list[str] = []
    for match in pattern.finditer(text):
        family, number = match.group(1).upper(), int(match.group(2))
        identifier = canonical.get((family, number), match.group(0).upper())
        if identifier not in seen:
            seen.append(identifier)
    return seen


def inherit(local: list[str], file_ids: list[str]) -> list[str]:
    """A test that names its own requirements proves those; one that names none inherits its file's.

    Without this a file whose `//!` header cites PF-57 would claim every case in it for PF-57,
    including the ones that name something else entirely, and the matrix would read as coverage
    where there is none.
    """
    return sorted(set(local)) if local else sorted(set(file_ids))


def scan_rust(text: str, repo: str, path: str, pattern, canonical) -> list[TestCase]:
    """`#[test]`/`#[tokio::test]` functions; ids come from the `///` block, or from the `//!` file
    header when the case names none itself."""
    lines = text.splitlines()
    header = "\n".join(line for line in lines if line.lstrip().startswith("//!"))
    file_ids = cited_ids(header, pattern, canonical)
    cases: list[TestCase] = []
    comments: list[str] = []
    attributes: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("//!"):
            continue  # the file header, already read into `file_ids`
        if stripped.startswith("//"):
            comments.append(stripped)
            continue
        if stripped.startswith("#["):
            attributes.append(stripped)
            continue
        if not stripped:
            comments = []  # a doc comment abuts its item; a blank line ends the block
            continue
        function = RUST_FN.match(line)
        if function and any(a.startswith(RUST_TEST_ATTRIBUTES) for a in attributes):
            ignored = [a for a in attributes if a.startswith("#[ignore")]
            local = cited_ids("\n".join(comments) + "\n" + "\n".join(attributes), pattern, canonical)
            cases.append(TestCase(
                repo=repo, path=path, line=number, name=function.group(1), kind="rust",
                lane=lane_for(repo, path),
                requirements=inherit(local, file_ids),
                skip="unconditional" if ignored else "", skip_note=" ".join(ignored),
            ))
        if stripped:
            comments, attributes = [], []
    return cases


def skip_of_typescript(modifiers: str) -> str:
    """`it.skip`/`it.todo` switch a case off; `it.skipIf`/`it.runIf` only bind it to an environment."""
    if ".skipIf" in modifiers or ".runIf" in modifiers:
        return "conditional"
    if ".skip" in modifiers or ".todo" in modifiers or ".fails" in modifiers:
        return "unconditional"
    return ""


def scan_typescript(text: str, repo: str, path: str, pattern, canonical) -> list[TestCase]:
    """vitest and Playwright `it`/`test`; ids come from the file header, the enclosing
    `describe` titles, the comment block above the case and the case title itself."""
    lines = text.splitlines()
    header = "\n".join(line for line in lines[:40] if line.lstrip().startswith("//"))
    file_ids = cited_ids(header, pattern, canonical)
    cases: list[TestCase] = []
    describes: list[tuple[int, str]] = []
    comments: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        described = TS_DESCRIBE.match(line)
        if described:
            indent = len(described.group(1))
            describes = [(width, title) for width, title in describes if width < indent]
            describes.append((indent, described.group(3)))
            comments = []
            continue
        tested = TS_TEST.match(line)
        if tested:
            indent, modifiers, title = len(tested.group(1)), tested.group(3) or "", tested.group(5)
            describes = [(width, title_) for width, title_ in describes if width < indent]
            context = "\n".join([*(t for _, t in describes), title, *comments])
            skip = skip_of_typescript(modifiers)
            cases.append(TestCase(
                repo=repo, path=path, line=number, name=title, kind="playwright" if "e2e" in path else "vitest",
                lane=lane_for(repo, path),
                requirements=inherit(cited_ids(context, pattern, canonical), file_ids),
                skip=skip,
                skip_note=f"{modifiers} {' '.join(comments[-2:])}".strip() if skip else "",
            ))
            comments = []
            continue
        if stripped.startswith("//"):
            comments.append(stripped)
        elif stripped:
            comments = []
    return cases


def _python_docstring(lines: list[str], start: int) -> str:
    """The docstring that opens the body at `lines[start:]`, or an empty string."""
    index = start
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return ""
    stripped = lines[index].strip()
    quote = stripped[:3]
    if quote not in TRIPLE_QUOTES:
        return ""
    rest = stripped[3:]
    if rest.endswith(quote):
        return rest[:-3]
    body = [rest]
    for line in lines[index + 1:]:
        if quote in line:
            body.append(line.split(quote)[0])
            break
        body.append(line)
    return "\n".join(body)


def scan_python(text: str, repo: str, path: str, pattern, canonical) -> list[TestCase]:
    """pytest functions; ids come from the module docstring, the decorators, the name and the
    function's own docstring."""
    lines = text.splitlines()
    file_ids = cited_ids(_python_docstring(lines, 0), pattern, canonical)
    cases: list[TestCase] = []
    decorators: list[str] = []
    comments: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if PY_DECORATOR.match(line):
            decorators.append(stripped)
            continue
        if stripped.startswith("#"):
            comments.append(stripped)
            continue
        defined = PY_DEF.match(line)
        if defined:
            signature_end = index
            while signature_end < len(lines) and not lines[signature_end].rstrip().endswith(":"):
                signature_end += 1
            docstring = _python_docstring(lines, signature_end + 1)
            skips = [d for d in decorators if "skip" in d or "xfail" in d]
            conditional = any("skipif" in d or "xfail(" in d for d in skips)
            context = "\n".join([defined.group(2), docstring, *decorators, *comments])
            cases.append(TestCase(
                repo=repo, path=path, line=index + 1, name=defined.group(2), kind="pytest",
                lane=lane_for(repo, path),
                requirements=inherit(cited_ids(context, pattern, canonical), file_ids),
                skip=("conditional" if conditional else "unconditional") if skips else "",
                skip_note=" ".join(skips),
            ))
        if stripped:
            decorators, comments = [], []
    return cases


def scan_robot(text: str, repo: str, path: str, pattern, canonical) -> list[TestCase]:
    """Robot cases; ids come from the suite `Documentation`/`Force Tags` and from the case's own
    `[Documentation]` and `[Tags]` lines."""
    lines = text.splitlines()
    section = ""
    settings: list[str] = []
    cases: list[TestCase] = []
    current: TestCase | None = None
    body: list[str] = []

    def close() -> None:
        if current is not None:
            context = "\n".join([current.name, *body])
            current.requirements = inherit(cited_ids(context, pattern, canonical),
                                           cited_ids("\n".join(settings), pattern, canonical))
            skips = [entry for entry in body if "robot:skip" in entry.lower()]
            current.skip = "unconditional" if skips else ""
            current.skip_note = " ".join(skips)
            cases.append(current)

    for number, line in enumerate(lines, start=1):
        heading = ROBOT_SECTION.match(line.strip())
        if heading:
            close()
            current, body = None, []
            section = heading.group(1).strip().lower()
            continue
        if section == "settings":
            settings.append(line)
            continue
        if section != "test cases":
            continue
        if line.strip() and not line[0].isspace():
            close()
            current, body = TestCase(repo=repo, path=path, line=number, name=line.strip(),
                                     kind="robot", lane=lane_for(repo, path)), []
        elif current is not None:
            body.append(line.strip())
    close()
    return cases


# What a citation in production code is read from. A test names a requirement to prove it; this
# is the other half — the code that implements it, which is what tells a "built but untested"
# requirement apart from one nobody has written yet (T-2142).
CODE_SUFFIXES = {".rs", ".ts", ".tsx", ".py", ".yaml", ".yml", ".sql", ".rego", ".sh"}


def is_test_file(path: str) -> bool:
    """Whether a path is a test rather than the code it tests."""
    name = path.rsplit("/", 1)[-1]
    parts = path.split("/")
    return (
        "tests" in parts
        or "test" in parts
        or "e2e" in parts
        or "benchmarks" in parts
        or name.startswith("test_")
        or name.endswith(".robot")
        or ".test." in name
        or ".spec." in name
        or name.startswith("selftest")
    )


def code_text(path: str, text: str) -> str:
    """The part of a file that is production code: a Rust test module is not."""
    if path.endswith(".rs"):
        cut = text.find("#[cfg(test)]")
        if cut != -1:
            return text[:cut]
    return text


def scan_code(repo: str, root: Path, pattern, canonical) -> dict[str, list[str]]:
    """Requirement ids named in a repository's production code, by id.

    A citation here is the code saying which requirement it implements — a doc comment, a
    policy rule, a chart annotation. It is weaker evidence than a test, and the matrix says so:
    `built` rather than `tested`.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        if is_test_file(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for identifier in cited_ids(code_text(relative, text), pattern, canonical):
            references = found.setdefault(identifier, [])
            if len(references) < 5:
                references.append(f"{repo}/{relative}")
    return found


def scan_repo(repo: str, root: Path, pattern, canonical) -> list[TestCase]:
    """Every test file of one repository, by the scanner its language needs."""
    cases: list[TestCase] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        name, suffix = path.name, path.suffix
        if suffix == ".rs":
            scanner = scan_rust
        elif suffix in {".ts", ".tsx"} and (".test." in name or ".spec." in name):
            scanner = scan_typescript
        elif suffix == ".py" and name.startswith("test_"):
            scanner = scan_python
        elif suffix == ".robot":
            scanner = scan_robot
        else:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{repo}/{relative}: unreadable, skipped ({exc})", file=sys.stderr)
            continue
        cases.extend(scanner(text, repo, relative, pattern, canonical))
    return cases


def head_of(root: Path) -> str:
    """The commit a scanned repository sat on, so a report names what it measured."""
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def build_index(docs: Path, repos: dict[str, Path]) -> dict:
    requirements = read_requirements(docs)
    if not requirements:
        raise SystemExit(f"no requirement found in {docs / 'Requirements'}: wrong --docs path?")
    canonical = {split_id(r.id): r.id for r in requirements}  # type: ignore[misc]
    # The prefix a citation is written with, which is the family for a plain id and the
    # chapter-level prefix for a two-level one.
    pattern = citation_pattern({split_id(r.id)[0] for r in requirements})  # type: ignore[index]

    cases: list[TestCase] = []
    implements: dict[str, list[str]] = {}
    for repo, root in sorted(repos.items()):
        cases.extend(scan_repo(repo, root, pattern, canonical))
        for identifier, references in scan_code(repo, root, pattern, canonical).items():
            implements.setdefault(identifier, []).extend(references)

    by_requirement: dict[str, list[int]] = {r.id: [] for r in requirements}
    unknown: dict[str, list[str]] = {}
    for position, case in enumerate(cases):
        for identifier in case.requirements:
            if identifier in by_requirement:
                by_requirement[identifier].append(position)
            else:
                unknown.setdefault(identifier, []).append(case.reference)

    return {
        "generated": date.today().isoformat(),
        "commits": {repo: head_of(root) for repo, root in sorted(repos.items())},
        "requirements": [asdict(r) for r in requirements],
        "tests": [asdict(c) for c in cases],
        "proves": {identifier: positions for identifier, positions in by_requirement.items() if positions},
        # Only for requirements that exist: an id named in code and nowhere in the requirements
        # is an unknown citation, and `unknown_citations` is where a test's is already reported.
        "implements": {
            identifier: references
            for identifier, references in sorted(implements.items())
            if identifier in by_requirement
        },
        "unknown_citations": {identifier: sorted(set(refs)) for identifier, refs in sorted(unknown.items())},
    }


# The committed page names a few tests per requirement and points at the index for the rest; the
# full join is 13 000 pairs, which is a machine's document, not a reader's.
TESTS_SHOWN = 3
FAMILY_TITLES = {
    "R": "Access control and federation", "GW": "Gateway firewall", "MIM": "OASC interoperability",
    "I": "Identity and credentials", "CC": "Configuration as code", "SP": "Context-space surface",
    "PF": "Platform invariants", "MF": "Manifests", "EP": "Endpoints and parity",
    "DM": "Data models", "MP": "Model projections", "PL": "Pipelines", "AG": "Agents and MCP",
    "AP": "Apps on demand", "SDK": "App SDK", "UI": "Portal and user interface",
    "TS": "Testing and quality", "OPS": "Operations and reliability", "DS": "Data space connector",
}
SHORT_REPO = {
    "joinedcontext-platform": "platform", "joinedcontext-portal": "portal",
    "joinedcontext-conformance": "conformance", "joinedcontext-deployment": "deployment",
    "docs": "docs",
}


def short(case: dict) -> str:
    return f"{SHORT_REPO.get(case['repo'], case['repo'])} `{case['path']}::{case['name']}`"


def state_of(identifier: str, index: dict) -> str:
    """What the platform has done about one requirement (T-2142).

    `tested` — a test names it, so a regression goes red. `built` — the code names it and no
    test does, which is a claim nobody checks. `open` — neither: a sentence in a document.
    """
    if index["proves"].get(identifier):
        return "tested"
    return "built" if index.get("implements", {}).get(identifier) else "open"


def render_matrix(index: dict) -> str:
    """The committed page: every requirement, its state, the tests that cite it and their lane."""
    tests = index["tests"]
    proves = index["proves"]
    implements = index.get("implements", {})
    requirements = index["requirements"]
    families: dict[str, list[dict]] = {}
    for requirement in requirements:
        families.setdefault(requirement["family"], []).append(requirement)

    lines = [
        "---",
        "sidebar_position: 21",
        'title: "Requirement Compliance Matrix"',
        "description: Every requirement with the named tests that prove it and the lane that runs them.",
        "---",
        "",
        "# Requirement Compliance Matrix",
        "",
        "This page is generated by `joinedcontext-conformance/scripts/compliance.py index` and "
        "committed, so a reader and an auditor see the same join the gate enforces: one row per "
        "requirement, the tests that name its id, and the lane that runs them. "
        "[Requirements Traceability](traceability.md) maps families to suites; this page maps a "
        "single requirement to single tests.",
        "",
        "## 1. How to read this page",
        "",
        f"Generated {index['generated']} from: "
        + ", ".join(f"`{SHORT_REPO.get(repo, repo)}` {sha[:7]}" for repo, sha in index["commits"].items())
        + ".",
        "",
        "- **Tags** are the ones `STYLE.md` defines: `[P]` performance, `[H]` human-facing, "
        "`[A]` agent-facing, `[S]` security.",
        "- **State** is one of three (T-2142). `tested`: at least one test names the requirement "
        "id, so a regression turns a lane red. `built`: the code names it — a doc comment, a "
        "policy rule, a chart — and no test does, so the claim is written down and unchecked. "
        "`open`: neither. A tested requirement is not a passing one: whether those tests passed "
        "is the compliance report, which reads the lanes' JUnit output.",
        "- **`open` measures citation, not behaviour.** A requirement can be built and still be "
        "`open` when nothing names its id — which is itself a traceability gap (TS-18), because "
        "the code that implements it cannot be found from the requirement. Cite the id in the "
        "test, or in the code when there is no test yet.",
        "- **Lane** is where the tests run: the fast `ci` merge gate, the hourly `ci-full`, the "
        "conformance workflows, or the suites that need a live cluster.",
        f"- At most {TESTS_SHOWN} tests are named per requirement. The complete join, with every "
        "file and line, is `compliance/index.json` in the conformance repository.",
        "",
        "## 2. Coverage by family",
        "",
        "| Family | Requirements | Tested | Built | Open | Untested and security-tagged |",
        "|---|---|---|---|---|---|",
    ]

    def counts(members: list[dict]) -> tuple[int, int, int, int]:
        states = [state_of(r["id"], index) for r in members]
        untested_security = [
            r for r in members if state_of(r["id"], index) != "tested" and "S" in r["tags"]
        ]
        return (
            states.count("tested"),
            states.count("built"),
            states.count("open"),
            len(untested_security),
        )

    for family, members in sorted(families.items()):
        tested, built, opened, security = counts(members)
        title = FAMILY_TITLES.get(family, family)
        lines.append(
            f"| **{family}** — {title} | {len(members)} | {tested} | {built} | {opened} | "
            f"{security} |"
        )
    tested, built, opened, security = counts(requirements)
    lines += [
        f"| **Total** | {len(requirements)} | {tested} | {built} | {opened} | {security} |",
        "",
        "## 3. Requirement to test",
        "",
    ]
    for family, members in sorted(families.items()):
        lines += [
            f"### {family} — {FAMILY_TITLES.get(family, family)}",
            "",
            "| Requirement | Tags | State | Lane | Tests, or the code that claims it |",
            "|---|---|---|---|---|",
        ]
        for requirement in members:
            identifier = requirement["id"]
            positions = proves.get(identifier, [])
            cases = [tests[position] for position in positions]
            lanes = sorted({case["lane"] for case in cases})
            state = state_of(identifier, index)
            if cases:
                named = ", ".join(short(case) for case in cases[:TESTS_SHOWN])
                if len(cases) > TESTS_SHOWN:
                    named += f", and {len(cases) - TESTS_SHOWN} more"
            elif state == "built":
                places = implements[identifier]
                named = ", ".join(f"`{place}`" for place in places[:TESTS_SHOWN])
                if len(places) > TESTS_SHOWN:
                    named += ", and more"
            else:
                named = ""
            lines.append(
                f"| **{identifier}** | {' '.join(f'[{t}]' for t in requirement['tags'])} | "
                f"{state} | {', '.join(lanes)} | {named} |"
            )
        lines.append("")
    lines += [
        "## Related",
        "",
        "- [Requirements Traceability](traceability.md) — the family-level matrix this page refines.",
        "- [Security tests](../Testing/06-security-tests.md) — the attack-vector gate that reads the same index.",
        "- [Testing strategy](../Testing/00-strategy.md) — what each lane runs and when.",
        "",
    ]
    return "\n".join(lines)


def check_index(index: dict, baseline: dict) -> list[str]:
    """The four rules that make the index a gate rather than a picture."""
    failures: list[str] = []
    tests = index["tests"]
    proves = index["proves"]
    unproven = [r for r in index["requirements"] if r["id"] not in proves]

    for requirement in unproven:
        if "S" in requirement["tags"]:
            failures.append(
                f"{requirement['id']} [S] ({requirement['source']}:{requirement['line']}) is a "
                f"security requirement that no test names: {requirement['statement'][:90]}"
            )

    for identifier, references in index["unknown_citations"].items():
        failures.append(
            f"{identifier} is cited by {len(references)} test(s) but no requirement of that id "
            f"exists in docs/Requirements; first: {references[0]}"
        )

    listed = {position for positions in proves.values() for position in positions}
    for position in sorted(listed):
        case = tests[position]
        if case["skip"] == "unconditional" and not TASK_ID.search(case["skip_note"] or ""):
            failures.append(
                f"{case['repo']}/{case['path']}:{case['line']} {case['name']} proves "
                f"{', '.join(case['requirements'])} and is switched off with no task id beside the "
                f"skip: {case['skip_note'] or '(no reason given)'}"
            )

    committed = baseline.get("uncited_requirements")
    if not isinstance(committed, int):
        failures.append("compliance/baseline.json carries no integer `uncited_requirements`")
    elif len(unproven) > committed:
        failures.append(
            f"{len(unproven)} requirements are cited by no test, above the committed baseline of "
            f"{committed}: coverage only goes down. Cite the new requirements in their tests, or "
            f"record a new baseline with the reason in the commit message."
        )
    return failures


@dataclass(frozen=True)
class Outcome:
    name: str
    status: str
    detail: str


def parse_junit(paths: list[Path]) -> list[Outcome]:
    """Every `<testcase>` of every JUnit file under `paths`, with the status the runner recorded."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.xml")))
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"{path}: no such JUnit file or directory")
    outcomes: list[Outcome] = []
    for file in files:
        try:
            root = ET.parse(file).getroot()
        except ET.ParseError as exc:
            raise SystemExit(f"{file}: not valid JUnit XML ({exc})")
        for case in root.iter("testcase"):
            status, detail = "passed", ""
            for child in case:
                if child.tag in {"failure", "error"}:
                    status = "failed"
                    detail = (child.attrib.get("message") or child.text or "").strip()[:200]
                    break
                if child.tag == "skipped":
                    status = "skipped"
                    detail = (child.attrib.get("message") or "").strip()[:200]
            name = case.attrib.get("name", "")
            classname = case.attrib.get("classname", "")
            outcomes.append(Outcome(name=f"{classname}::{name}" if classname else name,
                                    status=status, detail=detail))
    return outcomes


def normalise(name: str) -> str:
    return name.strip().lower()


def match_outcomes(index: dict, outcomes: list[Outcome]) -> dict[int, str]:
    """Join a JUnit case to the indexed test it ran.

    Every runner spells a case differently: cargo-nextest writes `crate::module::name`, vitest the
    `describe > it` title chain, pytest `package.module::name`, Robot the case name under its suite.
    The last segment is the one they agree on, so the join is on it.
    """
    by_name: dict[str, list[int]] = {}
    for position, case in enumerate(index["tests"]):
        by_name.setdefault(normalise(case["name"]), []).append(position)

    status_of: dict[int, str] = {}
    rank = {"passed": 0, "skipped": 1, "failed": 2}
    for outcome in outcomes:
        candidates = [outcome.name]
        for separator in ("::", " > ", ".", "/"):
            if separator in outcome.name:
                candidates.append(outcome.name.rsplit(separator, 1)[1])
        for candidate in candidates:
            positions = by_name.get(normalise(candidate))
            if not positions:
                continue
            for position in positions:
                previous = status_of.get(position)
                if previous is None or rank[outcome.status] > rank[previous]:
                    status_of[position] = outcome.status
            break
    return status_of


def verdicts(index: dict, status_of: dict[int, str]) -> dict[str, dict]:
    """Per requirement, the verdict of one run.

    Green means every test that cites the requirement ran in this input and passed. A run that
    carries only some of the lanes leaves the rest `partial`, which is the honest word for it: the
    requirement is not disproved and it is not proved either.
    """
    result: dict[str, dict] = {}
    for requirement in index["requirements"]:
        positions = index["proves"].get(requirement["id"], [])
        statuses = [status_of.get(position) for position in positions]
        if not positions:
            state = "uncited"
        elif any(status == "failed" for status in statuses):
            state = "failed"
        elif all(status == "passed" for status in statuses):
            state = "passed"
        elif any(status == "passed" for status in statuses):
            state = "partial"
        else:
            state = "unproven"
        result[requirement["id"]] = {
            "state": state,
            "tags": requirement["tags"],
            "tests": [
                {
                    "repo": index["tests"][position]["repo"],
                    "path": index["tests"][position]["path"],
                    "name": index["tests"][position]["name"],
                    "lane": index["tests"][position]["lane"],
                    "status": status_of.get(position) or "not in this run",
                }
                for position in positions
            ],
        }
    return result


STATE_WORDS = {
    "passed": "passed", "failed": "failed", "partial": "partly run",
    "unproven": "not run", "uncited": "no test",
}
REPORT_STYLE = """
:root { color-scheme: light dark; --line: #7a7a7a; }
body { font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; }
caption { text-align: left; font-weight: 700; padding-bottom: .5rem; }
th, td { border: 1px solid var(--line); padding: .35rem .5rem; text-align: left; vertical-align: top; }
th { background: #00000014; }
td.state { font-weight: 700; white-space: nowrap; }
td.passed { background: #1a7f371f; }
td.failed { background: #b420201f; }
td.partial, td.unproven, td.uncited { background: #8a6d001f; }
details summary { cursor: pointer; }
"""


def render_report_html(report: dict) -> str:
    """A page an auditor reads: every requirement, its verdict word, and the tests behind it.

    The verdict is a word in the cell, never only a colour, so it survives a greyscale print and a
    screen reader.
    """
    rows = []
    for identifier, verdict in report["requirements"].items():
        tests = "".join(
            f"<li>{html.escape(test['repo'])} <code>{html.escape(test['path'])}::"
            f"{html.escape(test['name'])}</code> — {html.escape(test['lane'])} — "
            f"{html.escape(test['status'])}</li>"
            for test in verdict["tests"]
        )
        detail = (f"<details><summary>{len(verdict['tests'])} test(s)</summary><ul>{tests}</ul></details>"
                  if tests else "no test names this requirement")
        tags = " ".join(f"[{tag}]" for tag in verdict["tags"])
        rows.append(
            f"<tr><th scope=\"row\"><code>{html.escape(identifier)}</code></th>"
            f"<td>{html.escape(tags)}</td>"
            f"<td class=\"state {verdict['state']}\">{STATE_WORDS[verdict['state']]}</td>"
            f"<td>{detail}</td></tr>"
        )
    totals = "".join(
        f"<tr><th scope=\"row\">{html.escape(STATE_WORDS[state])}</th><td>{count}</td></tr>"
        for state, count in report["totals"].items()
    )
    commits = ", ".join(f"{html.escape(repo)} {html.escape(sha[:7])}"
                        for repo, sha in report["commits"].items())
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>joinedcontext platform compliance report</title>\n"
        f"<style>{REPORT_STYLE}</style>\n</head>\n<body>\n"
        "<h1>joinedcontext platform compliance report</h1>\n"
        f"<p>Run of {html.escape(report['generated'])} over {report['junit_cases']} JUnit case(s) "
        f"from {html.escape(', '.join(report['junit_inputs']))}. Indexed from {commits}.</p>\n"
        f"<table><caption>Totals</caption><tbody>{totals}</tbody></table>\n"
        "<h2>Requirements</h2>\n"
        "<table><thead><tr><th scope=\"col\">Requirement</th><th scope=\"col\">Tags</th>"
        "<th scope=\"col\">Verdict</th><th scope=\"col\">Tests</th></tr></thead>\n"
        f"<tbody>{''.join(rows)}</tbody></table>\n</body>\n</html>\n"
    )


def build_report(index: dict, junit: list[Path]) -> dict:
    outcomes = parse_junit(junit)
    status_of = match_outcomes(index, outcomes)
    by_requirement = verdicts(index, status_of)
    totals: dict[str, int] = {state: 0 for state in STATE_WORDS}
    for verdict in by_requirement.values():
        totals[verdict["state"]] += 1
    return {
        "generated": date.today().isoformat(),
        "commits": index["commits"],
        "indexed": index["generated"],
        "junit_inputs": [str(path) for path in junit],
        "junit_cases": len(outcomes),
        "matched_tests": len(status_of),
        "totals": totals,
        "requirements": by_requirement,
    }


def resolve_repos(arguments: list[str], here: Path) -> dict[str, Path]:
    """`--repo name=path`, or the five repositories as they sit beside the conformance clone."""
    if arguments:
        repos: dict[str, Path] = {}
        for argument in arguments:
            name, separator, path = argument.partition("=")
            if not separator:
                raise SystemExit(f"--repo takes name=path, not {argument!r}")
            root = Path(path).resolve()
            if not root.is_dir():
                raise SystemExit(f"--repo {name}: {root} is not a directory")
            repos[name] = root
        return repos
    found = {name: (here / name) for name in DEFAULT_REPOS if (here / name).is_dir()}
    if not found:
        raise SystemExit(f"no repository found beside {here}: name them with --repo name=path")
    return found


def load_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{path}: no {what} here. Run `compliance.py index` first.")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: not valid JSON ({exc})")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path} ({len(text)} bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("index", "check", "report"),
                        help="build the index, gate on it, or render a run's report")
    parser.add_argument("--repo", action="append", default=[], metavar="NAME=PATH",
                        help="a repository to scan; repeatable")
    parser.add_argument("--docs", type=Path, help="the docs repository (default: the one beside this clone)")
    parser.add_argument("--index", type=Path, default=Path("compliance/index.json"),
                        help="index: where the index is written; the other commands always rescan")
    parser.add_argument("--baseline", type=Path, default=Path("compliance/baseline.json"))
    parser.add_argument("--matrix", type=Path, help="the generated matrix page in the docs repository")
    parser.add_argument("--record-baseline", action="store_true",
                        help="index: write today's uncited count into the baseline")
    parser.add_argument("--junit", type=Path, action="append", default=[], metavar="PATH",
                        help="report: a JUnit file or a directory of them; repeatable")
    parser.add_argument("--out-html", type=Path, default=Path("compliance/compliance-report.html"))
    parser.add_argument("--out-json", type=Path, default=Path("compliance/compliance-report.json"))
    arguments = parser.parse_args(argv)


    here = Path(__file__).resolve().parent.parent.parent

    def scan() -> dict:
        repos = resolve_repos(arguments.repo, here)
        docs = arguments.docs or repos.get("docs") or (here / "docs")
        if not (docs / "Requirements").is_dir():
            raise SystemExit(f"{docs}/Requirements is not a directory: name it with --docs")
        return build_index(docs, repos)

    if arguments.command == "index":
        index = scan()
        write(arguments.index, json.dumps(index, indent=1, sort_keys=False) + "\n")
        matrix = arguments.matrix
        if matrix is None:
            repos = resolve_repos(arguments.repo, here)
            docs = arguments.docs or repos.get("docs") or (here / "docs")
            matrix = docs / "Requirements" / "compliance-matrix.md"
        write(matrix, render_matrix(index))
        unproven = [r for r in index["requirements"] if r["id"] not in index["proves"]]
        if arguments.record_baseline:
            write(arguments.baseline, json.dumps({
                "recorded": index["generated"],
                "uncited_requirements": len(unproven),
                "note": "The count of requirements no test names. It only ever goes down; "
                        "compliance.py check fails when it rises (TS-19).",
            }, indent=1) + "\n")
        print(f"{len(index['requirements'])} requirements, {len(index['tests'])} tests, "
              f"{len(index['proves'])} proven, {len(unproven)} cited by no test "
              f"({len([r for r in unproven if 'S' in r['tags']])} of them security-tagged)")
        return 0

    if arguments.command == "check":
        # Always rescan. The index is generated, not committed: a 2.6 MB JSON rewritten on every
        # run is repo bloat, rebuilding it takes under a second, and a stale file on disk would
        # make the gate answer about a tree that is no longer there.
        index = scan()
        failures = check_index(index, load_json(arguments.baseline, "baseline"))
        for failure in failures:
            print(failure)
        if failures:
            print(f"\n{len(failures)} compliance failure(s) — TS-19 requires none", file=sys.stderr)
            return 1
        print("ok: every security requirement is named by a test, every citation resolves, "
              "no listed test is switched off without a task, and coverage did not fall")
        return 0

    if not arguments.junit:
        parser.error("report needs at least one --junit path")
    report = build_report(scan(), arguments.junit)
    write(arguments.out_json, json.dumps(report, indent=1) + "\n")
    write(arguments.out_html, render_report_html(report))
    print(" ".join(f"{STATE_WORDS[state]}={count}" for state, count in report["totals"].items()))
    return 1 if report["totals"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
