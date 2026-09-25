"""T-2800: scripts/budgets-dev.py decides the nightly performance budgets.

What it reads (k6's summary, the page measures, the Portal's answer histogram, kubectl top) is
fed here as fixtures. A budget broken once is a `skip` with its value, broken on two nights in a
row a `fail`; a measure that was not taken (a 429 wall, a page that never rendered, a node
over 85 % memory) is an `error`, never a number.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("budgets_dev", ROOT / "scripts" / "budgets-dev.py")
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)


def by_key(results):
    return {r["key"]: r["verdict"] for r in results}


def test_memory_is_the_fullest_node():
    top = "node-a   512m   6%   9000Mi   57%\nnode-b   900m   9%   14000Mi   88%\n"
    assert bd.memory_percent(top) == 88
    with pytest.raises(ValueError):
        bd.memory_percent("")


def k6(endpoint=None, space=None, endpoint_429=0, fails=0):
    metrics = {"checks": {"values": {"rate": 1.0, "passes": 100, "fails": fails}}}
    for name, values in (("endpoint", endpoint), ("space", space)):
        if values is not None:
            metrics[f"budget_{name}_read_ms"] = {"values": values}
    if endpoint_429:
        metrics["budget_endpoint_rate_limited"] = {"values": {"count": endpoint_429}}
    return {"metrics": metrics}


def test_k6_p95_per_surface():
    measured, missing = bd.k6_measures(k6({"p(95)": 120.5, "count": 2400}, {"p(95)": 310.0, "count": 2400}))
    assert measured == {"endpoint/p95": 120.5, "space/p95": 310.0} and missing == {}


def test_a_rate_limited_surface_is_not_measured_and_the_other_still_is():
    measured, missing = bd.k6_measures(k6({"p(95)": 5.0, "count": 1200}, {"p(95)": 150.0, "count": 2400}, endpoint_429=1200))
    assert measured == {"space/p95": 150.0}
    assert "429" in missing["endpoint/p95"]


def test_failed_reads_or_no_reads_are_not_a_latency():
    measured, missing = bd.k6_measures(k6({"p(95)": 50.0, "count": 10}, None, fails=3))
    assert measured == {}
    assert "3 reads did not answer 200" in missing["endpoint/p95"] and missing["space/p95"] == "k6 recorded no read"


def test_page_measures_convert_bytes_and_name_what_is_missing():
    pages = {"pages": [
        {"page": "spaces", "lcpMs": 1800, "cls": 0.02, "jsBytes": 512000, "loadMs": 2100},
        {"page": "models", "lcpMs": None, "cls": 0, "jsBytes": 1024, "loadMs": 900},
    ]}
    measured, missing = bd.page_measures(pages)
    assert measured["page/spaces/js"] == 500 and measured["page/spaces/lcp"] == 1800
    assert "page/models/lcp" in missing and measured["page/models/cls"] == 0
    assert missing["page/apps/js"] == "the page was not measured"


METRICS_A = """# TYPE jc_agent_answer_duration_seconds histogram
jc_agent_answer_duration_seconds_bucket{kind="chat",le="1"} 5
jc_agent_answer_duration_seconds_bucket{kind="chat",le="2"} 8
jc_agent_answer_duration_seconds_bucket{kind="chat",le="5"} 10
jc_agent_answer_duration_seconds_bucket{kind="chat",le="+Inf"} 10
jc_agent_answer_duration_seconds_count{kind="chat"} 10
"""
METRICS_B = 'jc_agent_answer_duration_seconds_bucket{kind="build",le="1"} 1\njc_agent_answer_duration_seconds_bucket{kind="build",le="+Inf"} 2\n'


def test_buckets_add_up_over_pods_and_label_sets():
    assert bd.answer_buckets([METRICS_A, METRICS_B]) == {"1": 6, "2": 8, "5": 10, "+Inf": 12}


def test_quantile_interpolates_like_prometheus():
    buckets = bd.answer_buckets([METRICS_A])
    assert bd.quantile(0.5, buckets) == pytest.approx(1.0)
    assert bd.quantile(0.95, buckets) == pytest.approx(4.25)
    # Past the last finite bucket, the answer is that bucket's bound, as histogram_quantile says.
    assert bd.quantile(0.99, {"1": 1, "+Inf": 10}) == 1.0
    assert bd.quantile(0.5, {"1": 0, "+Inf": 0}) is None


def test_since_counts_new_answers_and_survives_a_restart():
    before = {"1": 5, "+Inf": 10}
    assert bd.since({"1": 7, "+Inf": 14}, before) == {"1": 2, "+Inf": 4}
    assert bd.since({"1": 1, "+Inf": 2}, before) == {"1": 1, "+Inf": 2}
    assert bd.since({"1": 1, "+Inf": 2}, None) == {"1": 1, "+Inf": 2}


def test_one_night_over_is_a_skip_two_nights_a_fail():
    measured = {key: 0 for key in bd.BUDGETS}
    measured["endpoint/p95"] = 350
    measured["page/spaces/cls"] = 0.1  # at the ceiling is within it
    first = by_key(bd.verdicts(measured, {}, set(), {}))
    assert first["budget/endpoint/p95"] == "skip" and first["budget/page/spaces/cls"] == "pass"
    assert bd.broken(measured) == ["endpoint/p95"]
    second = by_key(bd.verdicts(measured, {}, set(bd.broken(measured)), {}))
    assert second["budget/endpoint/p95"] == "fail"


def test_not_measured_is_an_error_and_no_answer_is_a_skip():
    measured = {key: 0 for key in bd.BUDGETS if not key.startswith(("space/", "assistant/"))}
    results = by_key(bd.verdicts(measured, {"space/p95": "429"}, {"space/p95"},
                                 {"assistant/answer-p50": "none", "assistant/answer-p95": "none"}))
    assert results["budget/space/p95"] == "error"
    assert results["budget/assistant/answer-p50"] == "skip" and results["budget/assistant/answer-p95"] == "skip"
    assert set(results) == {f"budget/{key}" for key in bd.BUDGETS}


def test_main_keeps_a_history_and_fails_the_second_night(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", "/nonexistent")
    monkeypatch.setattr(bd, "ROOT", tmp_path)
    monkeypatch.setattr(bd, "profile_env", lambda: {"JC_DEV_NS": "dev"})
    slow = {"endpoint/p95": 400.0, "space/p95": 100.0}
    monkeypatch.setattr(bd, "measure_endpoints", lambda env, reports: (dict(slow), {}))
    pages = {f"page/{p}/{part}": 0.0 for p in bd.PAGES for part in ("lcp", "cls", "js")}
    monkeypatch.setattr(bd, "measure_pages", lambda env, reports: (dict(pages), {}))
    nights = iter([[METRICS_A], [METRICS_A.replace("} 10", "} 12")]])
    monkeypatch.setattr(bd, "portal_metrics", lambda env: next(nights))
    out, history = tmp_path / "s.json", tmp_path / "h.json"

    assert bd.main(["--out", str(out), "--history", str(history)]) == 0
    assert by_key(json.loads(out.read_text())["results"])["budget/endpoint/p95"] == "skip"
    assert bd.main(["--out", str(out), "--history", str(history)]) == 1
    summary = json.loads(out.read_text())
    assert summary["check"] == "budgets"
    assert by_key(summary["results"])["budget/endpoint/p95"] == "fail"
    runs = json.loads(history.read_text())["runs"]
    assert len(runs) == 2 and runs[-1]["broken"] == ["assistant/answer-p50", "endpoint/p95"]
    # The second night counts only the two answers since the first (both in the 5 s bucket).
    assert runs[-1]["values"]["assistant/answer-p50"] == pytest.approx(3.5)


def test_a_profile_that_fails_makes_every_budget_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", "/nonexistent")
    monkeypatch.setattr(bd, "ROOT", tmp_path)

    def broken_profile():
        raise RuntimeError("tests/dev.env.sh budgets failed")

    monkeypatch.setattr(bd, "profile_env", broken_profile)
    out = tmp_path / "s.json"
    assert bd.main(["--out", str(out), "--history", str(tmp_path / "h.json")]) == 1
    assert set(by_key(json.loads(out.read_text())["results"]).values()) == {"error"}
