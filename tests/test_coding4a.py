"""Offline tests for experiments/coding4a.py — grading only, no API, no SDK.

Builds a tiny QuixBugs-shaped fixture tree (python_programs /
correct_python_programs / json_testcases) and exercises program discovery,
testcase parsing (arity disambiguation), and subprocess grading.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from experiments.coding4a import grade, list_programs, load_cases, prepare_cases

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def quixbugs(tmp_path: Path) -> Path:
    """A miniature QuixBugs layout with two testcase-backed programs."""
    for d in ("python_programs", "correct_python_programs", "json_testcases"):
        (tmp_path / d).mkdir()

    # Two-argument program: buggy uses the wrong operator.
    _write(tmp_path, "add2", buggy="return a - b", correct="return a + b",
           params="a, b",
           cases=[[[1, 2], 3], [[0, 0], 0], [[5, 7], 12]])

    # Single argument that IS a list (flatten-style json: [[the_list], out]).
    _write(tmp_path, "double_list",
           buggy="return [x * 2 for x in xs[:-1]]",
           correct="return [x * 2 for x in xs]",
           params="xs",
           cases=[[[[1, 2, 3]], [2, 4, 6]], [[[]], []]])

    # A program with NO json testcases must be skipped by discovery.
    (tmp_path / "python_programs" / "graphy.py").write_text(
        "def graphy(node):\n    return node\n", encoding="utf-8")
    return tmp_path


def _write(root: Path, name: str, buggy: str, correct: str, params: str,
           cases: list) -> None:
    for d, body in (("python_programs", buggy), ("correct_python_programs", correct)):
        (root / d / f"{name}.py").write_text(
            f"def {name}({params}):\n    {body}\n", encoding="utf-8")
    lines = "\n".join(json.dumps(c) for c in cases)
    (root / "json_testcases" / f"{name}.json").write_text(lines + "\n", encoding="utf-8")


def test_list_programs_skips_missing_testcases(quixbugs: Path) -> None:
    assert list_programs(quixbugs) == ["add2", "double_list"]


def test_load_cases_multi_arg_arity(quixbugs: Path) -> None:
    cases = load_cases(quixbugs, "add2")
    assert cases[0] == ([1, 2], 3)  # two args unpacked


def test_load_cases_single_list_arg(quixbugs: Path) -> None:
    cases = load_cases(quixbugs, "double_list")
    # arity is 1, so the inner list is the one argument, not two args.
    assert cases[0] == ([[1, 2, 3]], [2, 4, 6])


def test_grade_correct_program_passes(quixbugs: Path) -> None:
    for name in ("add2", "double_list"):
        result = grade(quixbugs / "correct_python_programs" / f"{name}.py",
                       name, load_cases(quixbugs, name))
        assert result == {
            "passed": True,
            "n_cases": len(load_cases(quixbugs, name)),
            "cases_failed": [],
        }


def test_grade_buggy_program_fails(quixbugs: Path) -> None:
    result = grade(quixbugs / "python_programs" / "add2.py",
                   "add2", load_cases(quixbugs, "add2"))
    assert result["passed"] is False
    assert result["cases_failed"] == [0, 2]  # 1-2 != 3, 5-7 != 12; 0-0 == 0 passes
    assert result["n_cases"] == 3


def test_grade_generator_and_tuple_output(tmp_path: Path) -> None:
    prog = tmp_path / "pairs.py"
    prog.write_text(textwrap.dedent("""\
        def pairs(n):
            for i in range(n):
                yield (i, i)
        """), encoding="utf-8")
    # JSON expected can only express lists — tuples/generators must normalize.
    result = grade(prog, "pairs", [([2], [[0, 0], [1, 1]])])
    assert result["passed"] is True


def test_grade_crash_counts_as_failure(tmp_path: Path) -> None:
    prog = tmp_path / "boom.py"
    prog.write_text("def boom(x):\n    raise ValueError('no')\n", encoding="utf-8")
    result = grade(prog, "boom", [([1], 1)])
    assert result == {"passed": False, "n_cases": 1, "cases_failed": [0]}


def test_grade_timeout_counts_as_failure(tmp_path: Path) -> None:
    prog = tmp_path / "spin.py"
    prog.write_text(textwrap.dedent("""\
        def spin(x):
            while True:
                pass
        """), encoding="utf-8")
    result = grade(prog, "spin", [([1], 1)], timeout=2.0)
    assert result == {"passed": False, "n_cases": 1, "cases_failed": [0]}


def test_grade_sqrt_uses_epsilon_tolerance(tmp_path: Path) -> None:
    """QuixBugs grades sqrt with abs=epsilon (its stored expected values
    differ from the reference output by ~1e-5)."""
    prog = tmp_path / "sqrt.py"
    prog.write_text(textwrap.dedent("""\
        def sqrt(x, epsilon):
            approx = x / 2
            while abs(x - approx ** 2) > epsilon:
                approx = 0.5 * (approx + x / approx)
            return approx
        """), encoding="utf-8")
    # Reference computes 5.196176..., stored expected is 5.196164...
    result = grade(prog, "sqrt", [([27, 0.01], 5.196164639727311)])
    assert result["passed"] is True


def test_prepare_cases_drops_reference_infeasible(quixbugs: Path) -> None:
    # Make the reference itself fail case 1 (raise), keeping cases 0 and 2.
    (quixbugs / "correct_python_programs" / "add2.py").write_text(
        textwrap.dedent("""\
            def add2(a, b):
                if a == 0:
                    raise RuntimeError('reference cannot do this one')
                return a + b
            """), encoding="utf-8")
    cases, kept_ids, skipped = prepare_cases(quixbugs, "add2", timeout=5.0)
    assert skipped == [1]
    assert kept_ids == [0, 2]
    assert cases == [([1, 2], 3), ([5, 7], 12)]


def test_module_import_does_not_load_sdk() -> None:
    """Grading must be usable offline: importing the module must not pull in
    the Claude Agent SDK (it is imported lazily inside the runner)."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    code = ("import sys; import experiments.coding4a; "
            "sys.exit(1 if 'claude_agent_sdk' in sys.modules else 0)")
    proc = subprocess.run([sys.executable, "-c", code], env=env,
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
