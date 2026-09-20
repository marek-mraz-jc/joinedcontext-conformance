#!/usr/bin/env python3
"""Translation completeness and ICU integrity across the portal locales (T-0076, TS-14, UI-11, UI-12).

Three checks, in the order Testing/03-frontend-and-e2e-tests.md §4 names them:

1. key parity — every key of the reference bundle exists in every other locale, and no locale
   carries a key the reference dropped;
2. placeholder parity — every ICU argument used in a message exists in its translation, so a
   translated string never renders `{name}` or drops the value entirely;
3. ICU syntax — braces balance, and every `plural`/`select`/`selectordinal` block declares the
   `other` category ICU requires as the fallback.

With `--sources` it also asserts that every key used in the code as `t('…')` exists in the
reference bundle: a typo in a key renders the key itself to the user.

    check-i18n.py --locales ui/src/locales [--sources ui/src] [--reference en]
    check-i18n.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

# ICU argument: {name}, {count, plural, …}, {kind, select, …}. The name must be followed by `}`
# or `,` — otherwise a plural arm like `=0 {No errors}` would read as an argument called `No`.
ARGUMENT = re.compile(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\}|,\s*([a-zA-Z]+))")
# t('key'), t("key"), i18n.t(`key`) — the second argument, if any, is ignored
T_CALL = re.compile(r"""\bt\(\s*['"`]([A-Za-z0-9_.:-]+)['"`]""")
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}


def flatten(node: object, prefix: str = "") -> dict[str, str]:
    """Nested bundle -> {"form.submit": "Uložiť"}; i18next reads both shapes the same way."""
    flat: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    else:
        flat[prefix] = str(node)
    return flat


def arguments(message: str) -> set[str]:
    return {name for name, _ in ARGUMENT.findall(message)}


def unbalanced(message: str) -> bool:
    depth = 0
    for char in message:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


def missing_other(message: str) -> list[str]:
    """Every plural/select block needs an `other` arm; without it ICU raises at render time."""
    broken = []
    for match in ARGUMENT.finditer(message):
        name, kind = match.group(1), match.group(2)
        if kind not in {"plural", "select", "selectordinal"}:
            continue
        depth, end = 0, len(message)
        for index in range(match.start(), len(message)):
            if message[index] == "{":
                depth += 1
            elif message[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = message[match.end() : end]
        if not re.search(r"(^|[\s,])other\s*\{", body):
            broken.append(f"{{{name}, {kind}}}")
    return broken


def check(locale_dir: Path, reference: str, source_dir: Path | None) -> list[str]:
    bundles = sorted(locale_dir.glob("*.json"))
    if not bundles:
        return [f"no locale bundle found in {locale_dir}"]

    flat: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for bundle in bundles:
        try:
            flat[bundle.stem] = flatten(json.loads(bundle.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            errors.append(f"{bundle.name}: not valid JSON: {exc}")
    if reference not in flat:
        return errors + [f"reference locale {reference}.json is missing from {locale_dir}"]

    expected = flat[reference]
    if not expected:
        errors.append(f"{reference}.json carries no key at all")

    for locale, messages in sorted(flat.items()):
        for key, message in sorted(messages.items()):
            if unbalanced(message):
                errors.append(f"{locale}.{key}: unbalanced braces in {message!r}")
            for block in missing_other(message):
                errors.append(f"{locale}.{key}: {block} has no `other` category")

        if locale == reference:
            continue
        for key in sorted(set(expected) - set(messages)):
            errors.append(f"{locale}.json is missing {key} (UI-11: every locale is complete)")
        for key in sorted(set(messages) - set(expected)):
            errors.append(f"{locale}.json carries {key}, which {reference}.json does not define")
        for key in sorted(set(expected) & set(messages)):
            lost = arguments(expected[key]) - arguments(messages[key])
            if lost:
                errors.append(
                    f"{locale}.{key}: translation drops the argument(s) {', '.join(sorted(lost))}"
                )

    if source_dir is not None:
        used: dict[str, Path] = {}
        for path in sorted(source_dir.rglob("*")):
            if path.suffix in SOURCE_SUFFIXES and "node_modules" not in path.parts:
                for key in T_CALL.findall(path.read_text(encoding="utf-8", errors="replace")):
                    used.setdefault(key, path)
        for key, path in sorted(used.items()):
            if key not in expected:
                errors.append(f"{path}: t('{key}') has no entry in {reference}.json")

    return errors


GOOD = {
    "en": {"app": {"title": "Portal"}, "form": {"errors": "{count, plural, one {# error} other {# errors}}"},
           "auth": {"signedInAs": "Signed in as {name}"}},
    "sk": {"app": {"title": "Portál"}, "form": {"errors": "{count, plural, one {# chyba} few {# chyby} other {# chýb}}"},
           "auth": {"signedInAs": "Prihlásený ako {name}"}},
}


def selftest() -> int:
    """Every rule must fire on a bundle that breaks it, and none on one that does not."""
    cases: list[tuple[str, dict, str | None]] = [
        ("a complete pair of bundles", GOOD, None),
        ("a missing key", {"en": GOOD["en"], "sk": {"app": {"title": "Portál"}}}, "is missing auth.signedInAs"),
        ("an extra key", {"en": {"app": {"title": "Portal"}}, "sk": {"app": {"title": "Portál", "sub": "x"}}},
         "carries app.sub"),
        ("a dropped argument", {"en": {"a": "Signed in as {name}"}, "sk": {"a": "Prihlásený"}},
         "drops the argument(s) name"),
        ("unbalanced braces", {"en": {"a": "Hello {name"}}, "unbalanced braces"),
        ("a plural without `other`", {"en": {"a": "{count, plural, one {# error}}"}}, "has no `other` category"),
        # a plural arm that starts with a word is not an argument: {=0 {No errors}} once read as `No`
        ("plural arms that begin with a word",
         {"en": {"a": "{count, plural, =0 {No errors} other {# errors}}"},
          "sk": {"a": "{count, plural, =0 {Bez chýb} other {# chýb}}"}}, None),
    ]
    failures = []
    for name, bundles, expected in cases:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for locale, content in bundles.items():
                (directory / f"{locale}.json").write_text(json.dumps(content), encoding="utf-8")
            errors = check(directory, "en", None)
            if expected is None and errors:
                failures.append(f"{name}: reported {errors}")
            elif expected is not None and not any(expected in error for error in errors):
                failures.append(f"{name}: {expected!r} was not reported, got {errors}")

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "en.json").write_text(json.dumps({"app": {"title": "Portal"}}), encoding="utf-8")
        source = directory / "src"
        source.mkdir()
        (source / "App.tsx").write_text("const x = t('app.titel');\n", encoding="utf-8")
        if not any("t('app.titel')" in error for error in check(directory, "en", source)):
            failures.append("a key typo in the source was not reported")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: every locale rule fires on a bundle that breaks it and stays quiet on one that does not")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--locales", type=Path, help="directory holding <locale>.json bundles")
    parser.add_argument("--sources", type=Path, help="optional source tree scanned for t('key') calls")
    parser.add_argument("--reference", default="en", help="locale every other one is compared against")
    parser.add_argument("--selftest", action="store_true", help="check the checker itself")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if not args.locales:
        parser.error("--locales is required (or use --selftest)")

    errors = check(args.locales, args.reference, args.sources)
    for error in errors:
        print(error)
    if errors:
        print(f"\n{len(errors)} localisation problem(s) — TS-14 requires none", file=sys.stderr)
        return 1
    print(f"ok: every locale in {args.locales} is complete against {args.reference}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
