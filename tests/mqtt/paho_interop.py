#!/usr/bin/env python3
"""Eclipse Paho MQTT interoperability tests against an edge broker, gated by a baseline (T-4879).

    paho_interop.py run SUITE_DIR VERSION HOST PORT OUT.json   run client_test.py (3.1.1) or
                                                                client_test5.py (5) of the pinned
                                                                paho.mqtt.testing checkout
    paho_interop.py check RESULTS.json BASELINE.json VERSION    exit 1 on a case the baseline
                                                                passes that now fails, or on a
                                                                case the baseline does not know
    paho_interop.py --selftest                                  the gate goes red on a planted
                                                                regression

The upstream scripts read their target from module globals set in their own `__main__` block and
hand sys.argv to unittest.main, which refuses their options; `run` imports the module, sets the
same globals and runs its `Test` case with a result that records each case by name.
"""

from __future__ import annotations

import importlib
import json
import signal
import sys
import unittest
from pathlib import Path

MODULES = {"3.1.1": "client_test", "5": "client_test5"}
#: A case waiting longer on the broker counts as TIMEOUT (test_flow_control2 waits without end on
#: RMQTT 0.24.0 with the chart's listener).
CASE_SECONDS = 60


def _timed_out(signum, frame):
    raise TimeoutError(f"no answer within {CASE_SECONDS} s")


def run(suite_dir: str, version: str, host: str, port: int) -> dict[str, str]:
    sys.path.insert(0, suite_dir)
    mod = importlib.import_module(MODULES[version])
    prefix = "client_test5/" if version == "5" else ""
    mod.host, mod.port, mod.topic_prefix = host, port, prefix
    mod.topics = [
        prefix + t for t in ("TopicA", "TopicA/B", "Topic/C", "TopicA/C", "/TopicA")
    ]
    mod.wildtopics = [
        prefix + t for t in ("TopicA/+", "+/C", "#", "/#", "/+", "+/+", "TopicA/#")
    ]
    mod.nosubscribe_topics = ("test/nosubscribe",)
    outcomes: dict[str, str] = {}

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            signal.alarm(CASE_SECONDS)

        def stopTest(self, test):
            signal.alarm(0)
            super().stopTest(test)

        def addSuccess(self, test):
            super().addSuccess(test)
            outcomes[test._testMethodName] = "PASS"

        def addFailure(self, test, err):
            super().addFailure(test, err)
            outcomes[test._testMethodName] = "FAIL"

        def addError(self, test, err):
            super().addError(test, err)
            timed_out = err[0] is TimeoutError
            outcomes[getattr(test, "_testMethodName", str(test))] = (
                "TIMEOUT" if timed_out else "ERROR"
            )

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            outcomes[test._testMethodName] = "SKIP"

    signal.signal(signal.SIGALRM, _timed_out)
    suite = unittest.TestLoader().loadTestsFromTestCase(mod.Test)
    unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    return dict(sorted(outcomes.items()))


def check(results: dict[str, str], baseline: dict, version: str) -> list[str]:
    """What breaks the gate: a baseline PASS that is no longer one, or a case it does not know."""
    expected = baseline[version]
    errors = [
        f"{case}: {results.get(case, 'MISSING')}, the baseline passes it"
        for case, want in expected.items()
        if want == "PASS" and results.get(case) != "PASS"
    ]
    errors += [
        f"{case}: not in the baseline" for case in results if case not in expected
    ]
    return errors


def improvements(results: dict[str, str], baseline: dict, version: str) -> list[str]:
    expected = baseline[version]
    return [
        c
        for c, r in results.items()
        if r == "PASS" and expected.get(c, "PASS") != "PASS"
    ]


def self_test() -> None:
    baseline = {"5": {"test_basic": "PASS", "test_retained": "FAIL"}}
    assert check({"test_basic": "PASS", "test_retained": "FAIL"}, baseline, "5") == []
    assert check({"test_basic": "FAIL", "test_retained": "FAIL"}, baseline, "5"), (
        "a case the baseline passes that fails must go red"
    )
    assert check({"test_retained": "FAIL"}, baseline, "5"), (
        "a case that did not run must go red"
    )
    assert check(
        {"test_basic": "PASS", "test_retained": "FAIL", "test_new": "PASS"},
        baseline,
        "5",
    )
    assert improvements(
        {"test_basic": "PASS", "test_retained": "PASS"}, baseline, "5"
    ) == ["test_retained"]
    print("paho_interop selftest: ok")


def main(argv: list[str]) -> int:
    if argv == ["--selftest"]:
        self_test()
        return 0
    if len(argv) == 6 and argv[0] == "run" and argv[2] in MODULES:
        out = run(argv[1], argv[2], argv[3], int(argv[4]))
        Path(argv[5]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        return 0
    if len(argv) == 4 and argv[0] == "check" and argv[3] in MODULES:
        results = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        baseline = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        errors = check(results, baseline, argv[3])
        passed = sum(r == "PASS" for r in results.values())
        print(f"MQTT {argv[3]}: {passed} of {len(results)} Paho interop cases pass")
        for e in errors:
            print(f"- regression: {e}")
        for c in improvements(results, baseline, argv[3]):
            print(f"- now passes, ratchet the baseline: {c}")
        return 1 if errors else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
