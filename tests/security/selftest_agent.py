#!/usr/bin/env python3
"""Proof that Agent Runner security & sandbox suites catch violations (T-0080, T-0081).

Asserts that:
1. Untouched prompt-injection corpus tests pass and live legs skip by name.
2. Corrupting corpus (duplicate id, empty must_not, wrong expect) fails and names each.
3. Conforming runner manifest passes all sandbox isolation checks.
4. Leaky runner manifest fails AG-22, AG-26, AG-27, AG-28 naming socket, 0.0.0.0/0, gateway token, TTL.
5. Empty manifest directory fails workload/policy presence assertion.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml


def run_pytest(
    test_file: Path,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> tuple[int, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-q",
        "-rs",
        "-p",
        "no:cacheprovider",
    ]
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = proc.stdout + "\n" + proc.stderr
    return proc.returncode, combined


def main() -> int:
    sec_dir = Path(__file__).resolve().parent
    test_injection = sec_dir / "test_prompt_injection.py"
    test_sandbox = sec_dir / "test_agent_sandbox_isolation.py"
    fixtures_dir = sec_dir / "fixtures" / "agent-runner"

    # 1. Untouched corpus passes and deployment legs skip by name
    code, out = run_pytest(test_injection)
    if code != 0:
        print(f"FAIL case 1: prompt injection suite failed unexpectedly:\n{out}", file=sys.stderr)
        return 1
    if "passed" not in out:
        print(f"FAIL case 1: expected passed tests, got:\n{out}", file=sys.stderr)
        return 1
    if "MCP_URL is not set" not in out:
        print(f"FAIL case 1: MCP_URL skip reason not found in output:\n{out}", file=sys.stderr)
        return 1
    if "AGENT_RUNNER_URL is not set" not in out:
        print(f"FAIL case 1: AGENT_RUNNER_URL skip reason not found in output:\n{out}", file=sys.stderr)
        return 1
    for name in ("AGENT_TRANSCRIPT is not set", "AGENT_PROXY_URL is not set"):
        if name not in out:
            print(f"FAIL case 1: '{name}' skip reason not found in output:\n{out}", file=sys.stderr)
            return 1
    print("case 1 ok: corpus tests pass and live legs skip by name")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # 2. Corrupted corpus fails naming duplicate id, empty must_not, and wrong expect
        corpus_data = yaml.safe_load((sec_dir / "injection_corpus.yaml").read_text(encoding="utf-8"))
        vectors = corpus_data["vectors"]

        # Inject duplicate ID
        vectors[1]["id"] = vectors[0]["id"]
        # Inject empty must_not
        vectors[2]["must_not"] = []
        # Inject tool-parameter with wrong expect: literal
        tp_vec = next(v for v in vectors if v.get("placement") == "tool-parameter")
        tp_vec["expect"] = "literal"

        corrupted_corpus = tmp / "corrupted_corpus.yaml"
        corrupted_corpus.write_text(yaml.dump(corpus_data), encoding="utf-8")

        code, out = run_pytest(test_injection, extra_env={"INJECTION_CORPUS": str(corrupted_corpus)})
        if code == 0:
            print("FAIL case 2: corrupted corpus passed unexpectedly", file=sys.stderr)
            return 1
        if "Duplicate vector id" not in out and "duplicate id" not in out.lower():
            print(f"FAIL case 2: duplicate ID failure not reported:\n{out}", file=sys.stderr)
            return 1
        if "empty 'must_not'" not in out and "must_not" not in out:
            print(f"FAIL case 2: empty must_not failure not reported:\n{out}", file=sys.stderr)
            return 1
        if "AG-21 requires all hostile tool parameters to carry expect: refused" not in out:
            print(f"FAIL case 2: wrong expect on tool-parameter not reported:\n{out}", file=sys.stderr)
            return 1
        print("case 2 ok: corrupted corpus fails reporting duplicate id, empty must_not, and wrong expect")

        # 3. Conforming runner manifest passes all sandbox tests
        conforming_manifest = fixtures_dir / "conforming.yaml"
        code, out = run_pytest(
            test_sandbox, extra_env={"AGENT_RUNNER_MANIFEST": str(conforming_manifest)}
        )
        if code != 0:
            print(f"FAIL case 3: conforming runner manifest failed:\n{out}", file=sys.stderr)
            return 1
        if "passed" not in out:
            print(f"FAIL case 3: expected passed tests, got:\n{out}", file=sys.stderr)
            return 1
        print("case 3 ok: conforming runner manifest passes all sandbox tests")

        # 4. Leaky runner manifest fails AG-22, AG-26, AG-27, AG-28 naming specific violations
        leaky_manifest = fixtures_dir / "leaky.yaml"
        code, out = run_pytest(
            test_sandbox, extra_env={"AGENT_RUNNER_MANIFEST": str(leaky_manifest)}
        )
        if code == 0:
            print("FAIL case 4: leaky manifest passed unexpectedly", file=sys.stderr)
            return 1
        for req_test in ("test_ag22_", "test_ag26_", "test_ag27_", "test_ag28_"):
            if req_test not in out:
                print(f"FAIL case 4: {req_test} did not report failure:\n{out}", file=sys.stderr)
                return 1
        if "docker.sock" not in out:
            print(f"FAIL case 4: docker socket violation not named in report:\n{out}", file=sys.stderr)
            return 1
        if "0.0.0.0/0" not in out:
            print(f"FAIL case 4: 0.0.0.0/0 violation not named in report:\n{out}", file=sys.stderr)
            return 1
        if "jc_gateway_token" not in out.lower() and "gateway token" not in out.lower():
            print(f"FAIL case 4: literal gateway token violation not named:\n{out}", file=sys.stderr)
            return 1
        if "ttl" not in out.lower():
            print(f"FAIL case 4: missing TTL violation not named in report:\n{out}", file=sys.stderr)
            return 1
        print("case 4 ok: leaky manifest fails AG-22, AG-26, AG-27, AG-28 and names specific violations")

        # 5. Empty directory fails workload and policy presence test
        empty_dir = tmp / "empty_manifests"
        empty_dir.mkdir()
        code, out = run_pytest(test_sandbox, extra_env={"AGENT_RUNNER_MANIFEST": str(empty_dir)})
        if code == 0:
            print("FAIL case 5: empty manifest directory passed unexpectedly", file=sys.stderr)
            return 1
        if "test_the_manifest_set_holds_at_least_one_workload_and_one_networkpolicy" not in out:
            print(f"FAIL case 5: presence test not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 5 ok: empty manifest directory fails presence test")


    # 6. The AG-21 leg against the stub MCP server: the conforming stub validates tool arguments
    #    against its published schema, the broken one hands them to the tool unchecked.
    sys.path.insert(0, str(sec_dir.parent / "mcp"))
    from stub_mcp_server import serve  # noqa: E402

    for mode, must_pass in (("conforming", True), ("broken", False)):
        server, _ = serve(port=0, mode=mode, agent_token="agent-token")
        port = server.server_address[1]
        try:
            code, out = run_pytest(
                test_injection,
                extra_env={"MCP_URL": f"http://127.0.0.1:{port}/cs/ovzdusie/mcp", "MCP_TOOL": "query_entities"},
                extra_args=["-k", "live_mcp"],
            )
        finally:
            server.shutdown()
            server.server_close()
        if (code == 0) != must_pass:
            print(f"FAIL case 6: the {mode} MCP stub gave the wrong verdict on the AG-21 leg:\n{out}", file=sys.stderr)
            return 1
        if not must_pass and "unexpectedly succeeded" not in out:
            print(f"FAIL case 6: the broken stub failed for the wrong reason:\n{out}", file=sys.stderr)
            return 1
    print("case 6 ok: AG-21 refusals hold against the conforming MCP stub and fail against the broken one")


    # 7. The T-0608 transcript leg: the leaky run is red and names the host and the keys it
    #    carried out, the clean run with the same injection quoted as data is green.
    fixtures = sec_dir / "fixtures" / "agent-runner"
    code, out = run_pytest(
        test_injection,
        extra_env={"AGENT_TRANSCRIPT": str(fixtures / "transcript-leaky.json")},
        extra_args=["-k", "recorded_transcript"],
    )
    if code == 0:
        print("FAIL case 7: the leaky transcript passed unexpectedly", file=sys.stderr)
        return 1
    for name in ("evil.example", "JC_MODEL_KEY", "JC_AGENT_PROXY_TOKEN"):
        if name not in out:
            print(f"FAIL case 7: the leak '{name}' was not named:\n{out}", file=sys.stderr)
            return 1
    code, out = run_pytest(
        test_injection,
        extra_env={"AGENT_TRANSCRIPT": str(fixtures / "transcript-clean.json")},
        extra_args=["-k", "recorded_transcript"],
    )
    if code != 0:
        print(f"FAIL case 7: the clean transcript failed:\n{out}", file=sys.stderr)
        return 1
    print("case 7 ok: the leaky transcript is red naming host and keys, the clean one green")

    print("ok: agent prompt-injection corpus and sandbox isolation conformance verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
