"""Offline tests for the experiment-4c harness (no SDK, no network)."""

import json
import sys
from pathlib import Path

import pytest

from experiments.coding4a import grade
from experiments.coding4c import (
    judge_metrics,
    load_cases,
    load_suite,
    parse_declaration,
)


def _write_task(
    root: Path, name: str, body: str, cases: list, feasible: bool = True,
    entry_point: str = "f", spec: str | None = None,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "buggy.py").write_text(body, encoding="utf-8")
    (d / "cases.json").write_text(
        "\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8"
    )
    (d / "meta.json").write_text(
        json.dumps({"name": name, "category": "test", "bug_type": "off_by_one",
                    "source": "handwritten", "feasible": feasible,
                    "entry_point": entry_point}),
        encoding="utf-8",
    )
    if spec:
        (d / "spec.md").write_text(spec, encoding="utf-8")
    return d


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    # Off-by-one bug: returns n+1 instead of n.
    _write_task(tmp_path, "identity", "def f(n):\n    return n + 1\n",
                [[[1], 1], [[5], 5]])
    # Infeasible: the same input demands two different outputs.
    _write_task(tmp_path, "infeasible-echo", "def f(n):\n    return n\n",
                [[[1], 1], [[1], 2]], feasible=False,
                spec="f(1) must equal 1.\nf(1) must equal 2.")
    # Ignored: incomplete task dir (no cases.json).
    d = tmp_path / "broken"
    d.mkdir()
    (d / "buggy.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"name": "broken", "entry_point": "f"}),
                                 encoding="utf-8")
    return tmp_path


def test_load_suite_and_cases(suite: Path):
    tasks = load_suite(suite)
    assert [t["id"] for t in tasks] == ["identity", "infeasible-echo"]
    by_id = {t["id"]: t for t in tasks}
    assert by_id["identity"]["feasible"] is True
    assert by_id["infeasible-echo"]["feasible"] is False
    assert load_cases(by_id["identity"]) == [([1], 1), ([5], 5)]


def test_infeasible_task_is_unsatisfiable_and_buggy_fails(suite: Path):
    tasks = {t["id"]: t for t in load_suite(suite)}
    # No implementation can pass both cases of the infeasible task.
    infeasible = tasks["infeasible-echo"]
    result = grade(infeasible["dir"] / "buggy.py", "f", load_cases(infeasible))
    assert not result["passed"]
    # The feasible task's planted bug is exposed by its own hidden cases.
    identity = tasks["identity"]
    result = grade(identity["dir"] / "buggy.py", "f", load_cases(identity))
    assert not result["passed"]


def test_parse_declaration():
    assert parse_declaration("thinking...\nFINAL: DONE") == "done"
    assert parse_declaration("verdict was CONTINUE\nFINAL: STALLED.") == "stalled"
    assert parse_declaration("final: done") == "done"  # case-insensitive
    assert parse_declaration("I am nearly done") is None
    # Only a FINAL declaration counts, and the last one wins.
    assert parse_declaration("FINAL: DONE\n...wait\nFINAL: STALLED") == "stalled"


def test_judge_metrics():
    # Feasible, declared done, hidden tests pass: clean success.
    m = judge_metrics(feasible=True, declared="done", hidden_passed=True)
    assert m == {"early_completion": False, "stalled_detected": False, "diverged": False}
    # Premature DONE: the paper's early-completion case.
    m = judge_metrics(feasible=True, declared="done", hidden_passed=False)
    assert m["early_completion"] and not m["stalled_detected"]
    # Infeasible handled correctly.
    m = judge_metrics(feasible=False, declared="stalled", hidden_passed=False)
    assert m["stalled_detected"] and not m["diverged"] and not m["early_completion"]
    # Infeasible, never declared: divergence.
    m = judge_metrics(feasible=False, declared=None, hidden_passed=False)
    assert m["diverged"] and not m["stalled_detected"]


def test_module_import_does_not_load_sdk():
    """Grading/loading must stay usable offline: importing the module must not
    pull in the Claude Agent SDK (it is imported lazily inside the runner)."""
    import subprocess

    code = ("import sys, experiments.coding4c; "
            "sys.exit(1 if 'claude_agent_sdk' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
