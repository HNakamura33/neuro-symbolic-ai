"""Offline tests for experiments/oracle4b.py — the 4b killability oracle.

No API, no SDK: judging runs the driver in subprocesses against fixture
functions with hand-verifiable equivalent mutants.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.mutate import mutants
from experiments.oracle4b import (
    cached_killability,
    judge_mutant,
    killability,
    load_cache,
    rescore,
    summarize,
)

# The second clause of the guard is redundant (x >= 0 implies x >= -5 for
# every input), so every mutant that only weakens it is equivalent for ALL
# inputs, not merely the sampled ones. Mutant inventory (DFS source order):
#   0  boolean     and -> or       killable at x=-3  (guard becomes x >= -5)
#   1  comparison  >= -> >  (x>=0) killable at x=0
#   2  constant    0 -> 1          killable at x=0
#   3  constant    0 -> -1         killable at x=-1
#   4  comparison  >= -> >  (x>=-5)  EQUIVALENT (still implied by x >= 0)
#   5  constant    5 -> 6  (-> -6)   EQUIVALENT
#   6  constant    5 -> 4  (-> -4)   EQUIVALENT
#   7  constant    1 -> 2  (return 1)   killable at any x >= 0
#   8  constant    1 -> 0  (return 1)   killable at any x >= 0
#   9  constant    1 -> 2  (return -1)  killable at any x < 0
#   10 constant    1 -> 0  (return -1)  killable at any x < 0
SGN = '''def sgn(x):
    """1 when x >= 0, else -1; the second guard clause is redundant."""
    if x >= 0 and x >= -5:
        return 1
    return -1
'''

SGN_TASK = {"task_id": "sgn", "entry_point": "sgn", "correct_source": SGN, "spec": ""}
SGN_ORACLE = {"inputs": [[0], [-1], [-3], [3], [-10]], "atol": 0}


def test_sgn_mutant_inventory_is_as_documented() -> None:
    assert [m.description for m in mutants(SGN, "sgn")] == [
        "and -> or", ">= -> >", "0 -> 1", "0 -> -1", ">= -> >",
        "5 -> 6", "5 -> 4", "1 -> 2", "1 -> 0", "1 -> 2", "1 -> 0",
    ]


def test_killability_separates_equivalent_mutants() -> None:
    verdicts = killability(SGN_TASK, SGN_ORACLE, per_call_timeout=5.0)
    assert verdicts == [
        True, True, True, True,     # guard mutants the inputs distinguish
        False, False, False,        # constructively equivalent
        True, True, True, True,     # return-value shifts
    ]


def test_judge_mutant_timeout_counts_as_killable() -> None:
    correct = "def tick(n):\n    return n\n"
    spinner = "def tick(n):\n    while True:\n        pass\n"
    assert judge_mutant(
        correct, spinner, "tick", inputs=[[1]], atol=0,
        per_call_timeout=0.5, outer_timeout=30.0,
    ) is True


def test_judge_mutant_exception_counts_as_killable() -> None:
    correct = "def half(n):\n    return n // 2\n"
    crasher = "def half(n):\n    return n // 0\n"
    assert judge_mutant(
        correct, crasher, "half", inputs=[[4]], atol=0,
        per_call_timeout=5.0, outer_timeout=30.0,
    ) is True


def test_judge_mutant_skips_inputs_where_correct_errors() -> None:
    # The only input crashes the CANONICAL solution too, so it carries no
    # signal: the mutant must come out equivalent, not killable.
    correct = "def inv(x):\n    return 10 // x\n"
    mutant = "def inv(x):\n    return 11 // x\n"
    assert judge_mutant(
        correct, mutant, "inv", inputs=[[0]], atol=0,
        per_call_timeout=5.0, outer_timeout=30.0,
    ) is False


def test_judge_mutant_float_closeness_and_nan() -> None:
    # Tiny float drift within tolerance is NOT a behavioral difference…
    correct = "def scale(x):\n    return x * 0.1\n"
    drift = "def scale(x):\n    return x * 0.1 + 1e-12\n"
    assert judge_mutant(
        correct, drift, "scale", inputs=[[3.0]], atol=1e-6,
        per_call_timeout=5.0, outer_timeout=30.0,
    ) is False
    # …and NaN == NaN for oracle purposes (both diverge identically).
    correct_nan = "def f(x):\n    return float('nan')\n"
    assert judge_mutant(
        correct_nan, correct_nan, "f", inputs=[[1]], atol=0,
        per_call_timeout=5.0, outer_timeout=30.0,
    ) is False


def test_judge_mutant_deepcopies_arguments() -> None:
    # The function mutates its argument; without a fresh deep copy per call
    # the mutant would see the canonical run's leftovers and diverge falsely.
    correct = "def pop_last(xs):\n    xs.pop()\n    return len(xs)\n"
    assert judge_mutant(
        correct, correct, "pop_last", inputs=[[[1, 2, 3]]], atol=0,
        per_call_timeout=5.0, outer_timeout=30.0,
    ) is False


def test_rescore_normalizes_over_killable_only() -> None:
    record = {
        "task_id": "sgn", "condition": "llm", "model": "sonnet",
        "tests_pass_original": True,
        "kill_matrix": [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0],
        "mutation_score": 4 / 11,
        "boundary_score": 3 / 10,
        "n_mutants": 11,
    }
    verdicts: list = [True, True, True, True, False, False, False,
                      True, True, True, True]
    boundary = [m.boundary for m in mutants(SGN, "sgn")]
    out = rescore(record, verdicts, boundary)
    assert out["n_killable_mutants"] == 8
    assert out["n_equivalent_mutants"] == 3
    assert out["n_oracle_unknown"] == 0
    assert out["mutation_score_norm"] == pytest.approx(4 / 8)
    # boundary mask drops only index 0 (boolean); killable boundary = 7,
    # killed-and-killable boundary = kill[1,7,8] = 3
    assert out["boundary_score_norm"] == pytest.approx(3 / 7)
    assert out["mutation_score"] == record["mutation_score"]  # raw preserved


def test_rescore_counts_unknown_verdicts_as_killable() -> None:
    record = {
        "task_id": "t", "condition": "llm", "model": None,
        "tests_pass_original": True,
        "kill_matrix": [1, 0, 0], "mutation_score": 1 / 3, "n_mutants": 3,
    }
    out = rescore(record, [True, None, False], [True, True, True])
    assert out["n_killable_mutants"] == 2   # None stays in the denominator
    assert out["n_oracle_unknown"] == 1
    assert out["mutation_score_norm"] == pytest.approx(1 / 2)


def test_rescore_rejects_length_mismatch() -> None:
    record = {"task_id": "t", "kill_matrix": [1, 0]}
    with pytest.raises(ValueError, match="oracle judged"):
        rescore(record, [True], [True])


def test_cached_killability_hits_and_invalidates(tmp_path: Path) -> None:
    cache = {"sgn": {"n_mutants": 11, "killable": [True] * 11}}
    # Hit: the poisoned oracle would judge everything equivalent, so getting
    # all-True back proves no subprocess ran.
    poisoned = {"inputs": [], "atol": 0}
    assert cached_killability(SGN_TASK, poisoned, cache, 5.0, 30.0) == [True] * 11
    # Stale entry (wrong mutant count) is recomputed and rewritten.
    cache["sgn"]["n_mutants"] = 99
    verdicts = cached_killability(SGN_TASK, SGN_ORACLE, cache, 5.0, 30.0)
    assert verdicts[4:7] == [False, False, False]
    assert cache["sgn"] == {"n_mutants": 11, "killable": verdicts}
    # Round-trips through JSON.
    path = tmp_path / "killable.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    assert load_cache(path)["sgn"]["killable"] == verdicts
    assert load_cache(tmp_path / "missing.json") == {}


def test_summarize_reports_valid_suites_only() -> None:
    def rec(condition: str, valid: bool, raw: float, norm: float) -> dict:
        return {
            "model": "sonnet", "condition": condition,
            "tests_pass_original": valid,
            "mutation_score": raw, "mutation_score_norm": norm,
            "boundary_score": raw, "boundary_score_norm": norm,
        }

    table = summarize([
        rec("llm", True, 0.5, 1.0),
        rec("llm", False, 0.0, 0.0),    # invalid: hits validity, not means
        rec("nsai-testgen", True, 0.73, 0.9),
    ])
    llm_row = next(line for line in table.splitlines() if " llm" in line)
    assert " 0.50 " in llm_row           # validity rate 1/2
    assert " 0.500 " in llm_row          # raw mean over the valid suite only
    assert " 1.000" in llm_row           # normalized mean
    testgen_row = next(l for l in table.splitlines() if "nsai-testgen" in l)
    assert " 1.00 " in testgen_row
    assert " 0.900" in testgen_row
