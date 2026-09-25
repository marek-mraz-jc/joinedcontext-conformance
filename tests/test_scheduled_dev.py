"""T-2798: scripts/scheduled-dev.py turns the suites' JUnit reports into a tasks/file-failures summary.

The keys have to stay the same from run to run (the board deduplicates on them), a known ETSI
failure must not file a task, and a run that measured nothing (did not start, skipped every
case, exited red with no failing case, timed out) has to come out as `error`, never as a pass.
The dev cluster is not here: the suites are stand-in scripts and kubectl is a stub.
"""

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("scheduled_dev", ROOT / "scripts" / "scheduled-dev.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

ROBOT_XUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="Smoke" tests="3" errors="0" failures="2" skipped="0">
<testcase classname="Etsi.Smoke" name="001 Create Entity" time="0.1"></testcase>
<testcase classname="Etsi.Smoke" name="002 Query Entities" time="0.1"><failure message="500 != 200" type="AssertionError"/></testcase>
<testcase classname="Etsi.Smoke" name="003 Temporal" time="0.1"><failure message="known"/></testcase>
</testsuite>
"""
PYTEST_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
<testcase classname="test_mcp_protocol" name="test_initialize"/>
<testcase classname="test_mcp_protocol" name="test_tools_list"><error message="fixture 'mcp' failed">trace</error></testcase>
<testcase classname="test_mcp_isolation" name="test_hidden_attr"><skipped message="MCP_HIDDEN_ATTR is not set"/></testcase>
</testsuite></testsuites>
"""
SCHEMATHESIS_JUNIT = """<?xml version="1.0" ?>
<testsuites disabled="0" errors="0" failures="1" tests="2">
<testsuite name="schemathesis" tests="2"><testcase name="GET /api/v1/projects"/>
<testcase name="GET /api/v1/projects/{project}"><failure type="failure">Server error</failure></testcase>
</testsuite></testsuites>
"""


def reports(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def verdicts(results):
    return {r["key"]: r["verdict"] for r in results}


def test_robot_xunit_is_one_result_per_case_and_a_quarantined_failure_is_skipped(tmp_path):
    results = sd.suite_results("etsi", reports(tmp_path, **{"xunit.xml": ROBOT_XUNIT}), 1, "",
                               {("Smoke", "003 Temporal")})
    assert verdicts(results) == {
        "etsi/Etsi.Smoke::001 Create Entity": "pass",
        "etsi/Etsi.Smoke::002 Query Entities": "fail",
        "etsi/Etsi.Smoke::003 Temporal": "skip",
    }
    failed = next(r for r in results if r["verdict"] == "fail")
    assert failed["detail"] == "500 != 200" and failed["evidence"].endswith("xunit.xml")


def test_pytest_errors_and_skips_and_robot_output_xml_is_not_junit(tmp_path):
    results = sd.suite_results("mcp", reports(tmp_path, **{
        "junit.xml": PYTEST_JUNIT,
        "output.xml": '<robot><suite name="S"><test name="t"/></suite></robot>',
        "broken.xml": "<testsuite",
    }), 1, "")
    assert verdicts(results) == {
        "mcp/test_mcp_protocol::test_initialize": "pass",
        "mcp/test_mcp_protocol::test_tools_list": "error",
        "mcp/test_mcp_isolation::test_hidden_attr": "skip",
    }


def test_schemathesis_cases_without_classname_are_keyed_by_operation(tmp_path):
    results = sd.suite_results("schemathesis", reports(tmp_path, **{"junit-portal.xml": SCHEMATHESIS_JUNIT}), 1, "")
    assert verdicts(results) == {
        "schemathesis/GET /api/v1/projects": "pass",
        "schemathesis/GET /api/v1/projects/{project}": "fail",
    }


def test_the_same_report_gives_the_same_keys(tmp_path):
    first = sd.suite_results("mcp", reports(tmp_path, **{"junit.xml": PYTEST_JUNIT}), 1, "")
    second = sd.suite_results("mcp", tmp_path, 1, "")
    assert [r["key"] for r in first] == [r["key"] for r in second]


@pytest.mark.parametrize("files,code,title", [
    ({}, 1, "dsp on dev did not start: no test case was reported"),
    ({"junit.xml": '<testsuite><testcase name="a"><skipped/></testcase></testsuite>'}, 0,
     "dsp on dev measured nothing: every case skipped"),
    ({"junit.xml": '<testsuite><testcase name="a"/></testsuite>'}, 2, "dsp on dev exited 2 with no failing case"),
])
def test_a_run_that_measured_nothing_is_an_error(tmp_path, files, code, title):
    results = sd.suite_results("dsp", reports(tmp_path, **files), code, "DSP_URL: set DSP_URL to the connector URL")
    run = [r for r in results if r["key"] == "dsp/run"]
    assert run and run[0]["verdict"] == "error" and run[0]["title"] == title


def test_a_green_run_has_no_run_error(tmp_path):
    results = sd.suite_results("sta", reports(tmp_path, **{"junit.xml": '<testsuite><testcase name="a"/></testsuite>'}), 0, "")
    assert verdicts(results) == {"sta/a": "pass"}


def test_long_output_is_cut_to_its_tail(tmp_path):
    results = sd.suite_results("dsp", tmp_path, 1, "x" * 5000 + " the reason")
    assert len(results[0]["detail"]) <= sd.TAIL + 10 and results[0]["detail"].endswith("the reason")


def runner(tmp_path, body):
    script = tmp_path / "runner.sh"
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(0o755)
    return str(script)


def test_run_suite_reads_what_the_runner_wrote_and_drops_the_last_run(tmp_path, monkeypatch):
    out = tmp_path / "reports"
    out.mkdir()
    (out / "stale.xml").write_text('<testsuite><testcase name="old"><failure/></testcase></testsuite>')
    script = runner(tmp_path, 'printf \'<testsuite><testcase name="new"/></testsuite>\' > "$JC_REPORTS_DIR/junit.xml"\n')
    monkeypatch.setitem(sd.RUNNERS, "etsi", (script, (), 30))
    assert verdicts(sd.run_suite("etsi", out)) == {"etsi/new": "pass"}
    assert (out / "run.log").exists()


def test_a_timeout_is_an_error_and_stops_what_the_runner_started(tmp_path, monkeypatch):
    marker = tmp_path / "child-still-running"
    script = runner(tmp_path, f'(sleep 3; touch "{marker}") &\nsleep 30\n')
    monkeypatch.setitem(sd.RUNNERS, "etsi", (script, (), 1))
    results = sd.run_suite("etsi", tmp_path / "reports")
    assert verdicts(results) == {"etsi/run": "error"} and "timed out after 1 s" in results[0]["detail"]
    time.sleep(3.5)
    assert not marker.exists(), "the runner's child outlived the timeout"


def test_schemathesis_is_read_only_and_the_ttf_suite_is_never_scheduled():
    _, args, _ = sd.RUNNERS["schemathesis"]
    assert list(args) == ["--include-method", "GET", "--include-method", "HEAD"]
    scheduled = {s for suites in sd.SCHEDULES.values() for s in suites}
    assert "etsi-ttf" not in scheduled and scheduled == set(sd.RUNNERS)
    assert sd.RUNNERS["etsi"][0] == "scripts/etsi-smoke-dev.sh"


def test_a_suite_outside_the_schedule_and_a_missing_kubeconfig_are_refused(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        sd.main(["hourly", "--out", str(tmp_path / "s.json"), "--suites", "schemathesis"])
    monkeypatch.delenv("KUBECONFIG", raising=False)
    with pytest.raises(SystemExit):
        sd.main(["hourly", "--out", str(tmp_path / "s.json")])


def test_main_writes_the_summary_file_failures_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", "/nonexistent")
    monkeypatch.setattr(sd, "run_suite", lambda suite, out: [sd.result(f"{suite}/a", "pass", "ok")])
    out = tmp_path / "s.json"
    assert sd.main(["6h", "--out", str(out), "--suites", "mcp,ckan"]) == 0
    summary = json.loads(out.read_text())
    assert summary["check"] == "conformance" and summary["repo"] == "joinedcontext-conformance"
    assert [r["key"] for r in summary["results"]] == ["mcp/a", "ckan/a"]
    monkeypatch.setattr(sd, "run_suite", lambda suite, out: [sd.result(f"{suite}/run", "error", "did not start")])
    assert sd.main(["nightly", "--out", str(out)]) == 1


SEED = {"data": {
    "projects__a__spaces__s__endpoints__plain.yaml": "spec:\n  slug: plain1\n  enabledRepresentations: [ngsi-ld, geojson]\n",
    "projects__a__spaces__s__endpoints__stations.yaml": "spec:\n  slug: stations1\n  enabledRepresentations: [ngsi-ld, csv]\n  # stations are not sta\n",
    "projects__b__spaces__s__endpoints__sensors.yaml": "spec:\n  slug: sens1\n  enabledRepresentations: [ngsi-ld, sta, ogc-features]\n",
    "projects__b__spaces__s__pipelines__p.yaml": "spec:\n  slug: notanendpoint\n  enabledRepresentations: [sta]\n",
}}


def profile(tmp_path, suite, seed):
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    (tmp_path / "seed.json").write_text(json.dumps(seed))
    for name, body in {"kubectl": f'case "$*" in *"-o json"*) cat "{tmp_path}/seed.json";; esac\n', "curl": ""}.items():
        (stubs / name).write_text("#!/usr/bin/env bash\n" + body)
        (stubs / name).chmod(0o755)
    run = subprocess.run(
        ["bash", "-c", f'. tests/dev.env.sh {suite} >/dev/null 2>&1; echo "OGC=${{OGC_LANDING_URL:-}} STA=${{STA_URL:-}}"'],
        cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}"),
    )
    return run.stdout.strip()


def test_the_profile_finds_the_endpoint_that_enables_the_surface(tmp_path):
    assert profile(tmp_path, "ogc", SEED) == "OGC=https://dev.joinedcontext.com/api/endpoint/sens1/ogc/features STA="
    assert profile(tmp_path, "sta", SEED) == "OGC= STA=https://dev.joinedcontext.com/api/endpoint/sens1/sta/v1.1"


def test_no_endpoint_with_the_surface_leaves_the_url_unset(tmp_path):
    seed = {"data": {k: v for k, v in SEED["data"].items() if "sensors" not in k}}
    assert profile(tmp_path, "sta", seed) == "OGC= STA="
