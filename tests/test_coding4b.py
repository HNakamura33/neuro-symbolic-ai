"""Offline tests for experiments/mutate.py + experiments/coding4b.py.

No API, no SDK on the grading path: the mutation engine is pure ast, grading
runs pytest in subprocesses against fixture functions. The one SDK-touching
test (options wiring) imports the SDK in-process but performs no calls.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from experiments.coding4b import (
    count_tests,
    extract_test_source,
    grade_tests,
    load_dir,
    load_evalplus,
    sample_tasks,
)
from experiments.mutate import BOUNDARY_CLASSES, Mutant, Site, mutants, site_counts

REPO_ROOT = Path(__file__).resolve().parent.parent

# Fixture function covering all four operator classes. Mutant inventory
# (deterministic DFS source order):
#   0  boolean     or -> and          line 3
#   1  comparison  <= -> <            line 3
#   2  constant    0 -> 1   (d <= 0)  line 3
#   3  constant    0 -> -1  (d <= 0)  line 3
#   4  comparison  < -> <=            line 3
#   5  constant    0 -> 1   (n < 0)   line 3
#   6  constant    0 -> -1  (n < 0)   line 3
#   7  constant    0 -> 1   (return)  line 4
#   8  constant    0 -> -1  (return)  line 4
#   9  arithmetic  // -> *            line 5
#   10 arithmetic  * -> //            line 5
#   11 constant    100 -> 101         line 5
#   12 constant    100 -> 99          line 5
RATE = '''def rate(n, d):
    """Integer percentage n/d; 0 when d <= 0 or n < 0."""
    if d <= 0 or n < 0:
        return 0
    return n * 100 // d
'''

# Kills mutants 0, 7, 8, 9, 10, 12 (verified by hand and by execution):
# rate(1,2)==50 kills the arithmetic swaps and 100->99 (99//2 == 49), but NOT
# 100->101 (101//2 == 50, an equivalent-on-this-input mutant); rate(5,-1)==0
# kills or->and (falls through to 5*100//-1 == -500) and the return-0 shifts.
# No test hits d==0 or d==1, so every comparison/constant-shift on the guard
# survives.
RATE_TESTS = '''from target import rate

def test_simple():
    assert rate(1, 2) == 50

def test_negative_d():
    assert rate(5, -1) == 0
'''


# ---------------------------------------------------------------------------
# mutation engine
# ---------------------------------------------------------------------------


def test_mutants_operator_inventory() -> None:
    ms = mutants(RATE, "rate")
    assert len(ms) == 13
    assert Counter(m.op_class for m in ms) == {
        "constant": 8, "comparison": 2, "arithmetic": 2, "boolean": 1,
    }
    descriptions = [m.description for m in ms]
    assert "<= -> <" in descriptions
    assert "< -> <=" in descriptions
    assert "or -> and" in descriptions
    assert "// -> *" in descriptions
    assert "* -> //" in descriptions
    assert "100 -> 101" in descriptions
    assert "100 -> 99" in descriptions
    assert descriptions.count("0 -> 1") == 3  # two guards + return 0
    assert descriptions.count("0 -> -1") == 3


def test_mutants_boundary_tagging() -> None:
    ms = mutants(RATE, "rate")
    assert BOUNDARY_CLASSES == {"comparison", "constant"}
    boundary = [m for m in ms if m.boundary]
    assert len(boundary) == 10  # 2 comparison swaps + 8 constant shifts
    assert {m.op_class for m in boundary} == {"comparison", "constant"}
    assert all(not m.boundary for m in ms if m.op_class in ("arithmetic", "boolean"))


def test_mutants_deterministic_ordering() -> None:
    first, second = mutants(RATE, "rate"), mutants(RATE, "rate")
    assert first == second  # same sources, op classes, sites, order
    # DFS source order: boolean guard first, arithmetic return last-ish.
    assert first[0].op_class == "boolean"
    assert [m.description for m in first[:2]] == ["or -> and", "<= -> <"]


def test_mutant_sites_point_into_original_source() -> None:
    ms = mutants(RATE, "rate")
    assert ms[0].site == Site(lineno=3, col=7)  # the guard BoolOp
    assert all(m.site.lineno in (3, 4, 5) for m in ms)


def test_mutant_sources_compile_and_change_one_site() -> None:
    ms = mutants(RATE, "rate")
    assert len({m.source for m in ms}) == len(ms)  # all distinct
    for m in ms:
        compile(m.source, "<mutant>", "exec")


def test_mutants_skip_annotations_and_docstring() -> None:
    src = (
        "def shifty(x: int = 0) -> int:\n"
        '    """Docstring mentioning 1 < 2 and 3 + 4 stays untouched."""\n'
        "    y: 1 + 1 = x\n"  # annotation contains const/arith sites: skipped
        "    return y\n"
    )
    ms = mutants(src, "shifty")
    # Only the default value 0 is mutable (defaults are behavior).
    assert [m.description for m in ms] == ["0 -> 1", "0 -> -1"]
    assert all(m.op_class == "constant" for m in ms)
    for m in ms:
        assert "1 < 2 and 3 + 4 stays untouched" in m.source  # docstring intact
        assert "1 + 1" in m.source  # annotation intact


def test_mutants_scope_limited_to_entry_point() -> None:
    src = (
        "def helper(a):\n"
        "    return a + 1\n"
        "\n"
        "def main_fn(a):\n"
        "    return helper(a) * 2\n"
    )
    ms = mutants(src, "main_fn")
    assert sorted(m.description for m in ms) == ["* -> //", "2 -> 1", "2 -> 3"]
    for m in ms:  # helper is present but unmutated in every mutant module
        assert "return a + 1" in m.source


def test_mutants_ignore_bool_and_nonint_constants() -> None:
    src = (
        "def flaggy(x):\n"
        "    if x == 'a':\n"
        "        return True\n"
        "    return False\n"
    )
    ms = mutants(src, "flaggy")
    assert [m.description for m in ms] == ["== -> !="]


def test_mutants_missing_entry_point_raises() -> None:
    with pytest.raises(ValueError, match="nope"):
        mutants("def f(x):\n    return x\n", "nope")


def test_site_counts_matches_mutants() -> None:
    counts = site_counts(RATE, "rate")
    assert counts == {"boolean": 1, "comparison": 2, "constant": 8, "arithmetic": 2}
    assert sum(counts.values()) == len(mutants(RATE, "rate"))


# ---------------------------------------------------------------------------
# grading pipeline (subprocess pytest, offline)
# ---------------------------------------------------------------------------


def test_grade_end_to_end_exact_scores() -> None:
    ms = mutants(RATE, "rate")
    graded = grade_tests(RATE_TESTS, RATE, ms, timeout=30.0)
    assert graded["tests_pass_original"] is True
    assert graded["kill_matrix"] == [1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 1]
    assert graded["mutation_score"] == pytest.approx(6 / 13)
    assert graded["boundary_score"] == pytest.approx(3 / 10)
    assert graded["n_mutants"] == 13
    assert graded["n_boundary_mutants"] == 10
    by_desc = {
        (m.description, m.site.lineno): k
        for m, k in zip(ms, graded["kill_matrix"])
    }
    assert by_desc[("<= -> <", 3)] == 0     # boundary survivor: no d==0 test
    assert by_desc[("// -> *", 5)] == 1
    assert by_desc[("100 -> 101", 5)] == 0  # equivalent on the tested inputs
    assert by_desc[("or -> and", 3)] == 1


def test_grade_without_test_source_kills_nothing() -> None:
    ms = mutants(RATE, "rate")
    graded = grade_tests(None, RATE, ms, timeout=5.0)
    assert graded == {
        "tests_pass_original": False,
        "kill_matrix": [0] * 13,
        "mutation_score": 0.0,
        "boundary_score": 0.0,
        "n_mutants": 13,
        "n_boundary_mutants": 10,
    }


def test_grade_no_tests_collected_kills_nothing() -> None:
    # pytest exit code 5 (nothing collected) must not count as a kill —
    # otherwise an empty file would score 100%.
    ms = mutants(RATE, "rate")[:2]
    graded = grade_tests("from target import rate\n", RATE, ms, timeout=30.0)
    assert graded["tests_pass_original"] is False
    assert graded["kill_matrix"] == [0, 0]
    assert graded["mutation_score"] == 0.0


def test_grade_tests_failing_on_original_are_flagged() -> None:
    ms = mutants(RATE, "rate")[:1]
    wrong = "from target import rate\n\ndef test_wrong():\n    assert rate(1, 2) == 51\n"
    graded = grade_tests(wrong, RATE, ms, timeout=30.0)
    assert graded["tests_pass_original"] is False  # invalid suite, flagged


def test_grade_timeout_counts_as_killed() -> None:
    correct = "def tick(n):\n    return n\n"
    spinner = Mutant(
        source="def tick(n):\n    while True:\n        pass\n",
        op_class="comparison", site=Site(2, 4), description="manual",
    )
    tests = "from target import tick\n\ndef test_tick():\n    assert tick(1) == 1\n"
    graded = grade_tests(tests, correct, [spinner], timeout=3.0)
    assert graded["tests_pass_original"] is True
    assert graded["kill_matrix"] == [1]
    assert graded["mutation_score"] == 1.0


# ---------------------------------------------------------------------------
# extraction / counting
# ---------------------------------------------------------------------------


def test_extract_prefers_last_block_with_tests() -> None:
    text = (
        "Here is a helper:\n```python\nx = 1\n```\n"
        "and the tests:\n```python\nfrom target import rate\n\n"
        "def test_a():\n    assert rate(1, 2) == 50\n```\nDone."
    )
    src = extract_test_source(text)
    assert src is not None
    assert src.startswith("from target import rate")
    assert "def test_a" in src


def test_extract_falls_back_to_last_block_and_none() -> None:
    assert extract_test_source("no code here") is None
    assert extract_test_source("```py\nimport os\n```") == "import os\n"


def test_count_tests() -> None:
    src = (
        "import pytest\n\n"
        "def helper():\n    pass\n\n"
        "def test_a():\n    pass\n\n"
        "class TestGroup:\n    def test_b(self):\n        pass\n"
    )
    assert count_tests(src) == 2
    assert count_tests(None) == 0
    assert count_tests("def test_broken(:\n") == 0  # syntax error -> 0


# ---------------------------------------------------------------------------
# task sources
# ---------------------------------------------------------------------------


def test_load_dir_bugsuite_layout(tmp_path: Path) -> None:
    task_dir = tmp_path / "pager"
    task_dir.mkdir()
    (task_dir / "correct.py").write_text(
        "def pages(n, size=10):\n"
        '    """Number of pages for n items."""\n'
        "    if n <= 0:\n"
        "        return 0\n"
        "    return (n + size - 1) // size\n",
        encoding="utf-8",
    )
    (task_dir / "meta.json").write_text(
        json.dumps({"entry_point": "pages"}), encoding="utf-8"
    )
    (tmp_path / "not-a-task.txt").write_text("ignored", encoding="utf-8")

    tasks = load_dir(tmp_path)
    assert [t["task_id"] for t in tasks] == ["pager"]
    task = tasks[0]
    assert task["entry_point"] == "pages"
    assert "def pages(n, size=10):" in task["spec"]
    assert "Number of pages" in task["spec"]
    assert "// size" not in task["spec"]  # spec never leaks the implementation
    assert mutants(task["correct_source"], "pages")  # gradeable


def _write_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_evalplus_humaneval_from_cache(tmp_path: Path) -> None:
    """A cached release file is parsed as-is — no network involved."""
    prompt = (
        "def add_one(x):\n"
        '    """Return x plus one.\n    >>> add_one(1)\n    2\n    """\n'
    )
    _write_gz(tmp_path / "HumanEvalPlus.jsonl.gz", [{
        "task_id": "HumanEval/0",
        "entry_point": "add_one",
        "prompt": prompt,
        "canonical_solution": "    return x + 1\n",
    }])
    tasks = load_evalplus("humaneval", tmp_path)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_id"] == "HumanEval-0"
    assert task["spec"] == prompt  # HumanEval prompt IS the spec
    assert "return x + 1" in task["correct_source"]
    assert [m.description for m in mutants(task["correct_source"], "add_one")] == [
        "+ -> -", "1 -> 2", "1 -> 0",
    ]


def test_load_evalplus_mbpp_synthesizes_spec(tmp_path: Path) -> None:
    _write_gz(tmp_path / "MbppPlus.jsonl.gz", [{
        "task_id": "Mbpp/2",
        "entry_point": "double",
        "prompt": "\nWrite a function to double a number.\nassert double(2) == 4\n",
        "canonical_solution": "def double(x):\n    return x * 2\n",
    }])
    task = load_evalplus("mbpp", tmp_path)[0]
    assert task["task_id"] == "Mbpp-2"
    assert task["spec"].startswith("def double(x):")
    assert "Write a function to double a number." in task["spec"]
    assert "return x * 2" not in task["spec"]  # implementation stays hidden
    assert task["correct_source"] == "def double(x):\n    return x * 2\n"


def _task(task_id: str, source: str, entry_point: str) -> dict:
    return {
        "task_id": task_id, "entry_point": entry_point,
        "correct_source": source, "spec": "",
    }


def test_sample_tasks_boundary_filter_and_determinism() -> None:
    heavy = "def h{i}(x):\n    if x <= 0:\n        return 0\n    return x - 1\n"
    light = "def light(x):\n    return x * x\n"      # 1 mutant, 0 boundary
    empty = "def empty(x):\n    return x\n"          # no mutation sites
    broken = "def broken(x:\n"                       # unparseable
    tasks = [
        *[_task(f"h{i}", heavy.replace("{i}", str(i)), f"h{i}") for i in range(5)],
        _task("light", light, "light"),
        _task("empty", empty, "empty"),
        _task("broken", broken, "broken"),
    ]
    # No sampling: unparseable/site-free tasks drop, everything else stays.
    kept = sample_tasks(tasks, None, seed=1)
    assert [t["task_id"] for t in kept] == ["h0", "h1", "h2", "h3", "h4", "light"]
    # Sampling: only boundary-heavy tasks eligible (each h* has 5 boundary
    # mutants: <= swap plus four constant shifts; light has none).
    picked = sample_tasks(tasks, 2, seed=7, min_boundary=3)
    assert len(picked) == 2
    assert all(t["task_id"].startswith("h") for t in picked)
    assert picked == sample_tasks(tasks, 2, seed=7, min_boundary=3)  # seeded
    assert sample_tasks(tasks, 99, seed=7, min_boundary=3) == [
        t for t in kept if t["task_id"].startswith("h")
    ]  # n > eligible -> all eligible


# ---------------------------------------------------------------------------
# condition wiring (imports the SDK in-process; no API calls)
# ---------------------------------------------------------------------------


def test_build_task_options_llm_is_text_only(tmp_path: Path) -> None:
    from experiments.coding4b import build_task_options

    options = build_task_options("llm", "sonnet", tmp_path, tmp_path / "kb.ttl")
    # With dontAsk, tools=[] is what actually removes the built-in tool set;
    # allowed_tools=[] alone would restrict nothing (the documented pitfall).
    assert options.tools == []
    assert options.allowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert not options.mcp_servers
    assert options.agents is None


def test_build_task_options_nsai_wires_test_generator(tmp_path: Path) -> None:
    from experiments.coding4b import build_task_options

    options = build_task_options("nsai-testgen", "sonnet", tmp_path, tmp_path / "kb.ttl")
    assert sorted(options.tools) == ["Read", "Task"]  # built-in set limited
    assert list(options.agents) == ["test-generator"]  # no loop-judge/Bash
    assert "symbolic" in options.mcp_servers
    assert options.permission_mode == "dontAsk"
    assert any(t.startswith("mcp__symbolic__smt_verify") for t in options.allowed_tools)
    assert "Task" in options.allowed_tools
    # the subagent itself is solver+Read only
    assert "Bash" not in (options.agents["test-generator"].tools or [])


def test_module_import_does_not_load_sdk() -> None:
    """Grading must be usable offline: importing the module must not pull in
    the Claude Agent SDK or nsai (they are imported lazily in the runner)."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    code = (
        "import sys; import experiments.coding4b; "
        "sys.exit(1 if ('claude_agent_sdk' in sys.modules or 'nsai' in sys.modules) "
        "else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env,
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
