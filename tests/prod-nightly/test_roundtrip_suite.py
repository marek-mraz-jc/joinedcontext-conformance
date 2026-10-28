"""T-5914: the prod nightly's roundtrip suite is the read half only, and only when it is configured."""

import os
import subprocess
from pathlib import Path

RUN = Path(__file__).resolve().parent / "run.sh"


def nightly(tmp_path, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("ROUNDTRIP_")}
    done = subprocess.run(
        ["bash", str(RUN)],
        env={**base, "ANTARES_DIR": str(tmp_path), "OUT": str(tmp_path / "out"), "SUITES": "roundtrip", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return done.returncode, (tmp_path / "out" / "report.md").read_text()


def test_unconfigured_roundtrip_is_listed_and_green(tmp_path):
    code, report = nightly(tmp_path)
    assert code == 0, report
    assert "not configured: set ROUNDTRIP_ENDPOINT and ROUNDTRIP_ENTITY" in report


def test_an_endpoint_that_is_no_id_is_red_and_nothing_is_called(tmp_path):
    code, report = nightly(tmp_path, ROUNDTRIP_ENDPOINT="../x", ROUNDTRIP_ENTITY="urn:ngsi-ld:T:1")
    assert code == 1, report
    assert "ROUNDTRIP_ENDPOINT is not an Endpoint id" in report
    assert not (tmp_path / "out" / "roundtrip.txt").exists()


def test_the_read_half_never_writes():
    text = RUN.read_text()
    block = text[text.index("if want roundtrip"):text.index('note "## Verdict')]
    assert "--read" in block and "NGSILD_TOKEN" not in block
