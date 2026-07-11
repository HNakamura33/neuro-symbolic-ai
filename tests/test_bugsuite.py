"""Mechanical validation of bugsuite/ (bugsuite/README.md) — offline.

For EVERY feasible task: correct.py passes all cases.json cases under the
experiment-4a subprocess grader, buggy.py fails at least one, and meta.json
is well-formed. For every infeasible variant (実験4c): cases.json is provably
unsatisfiable (the same input appears twice with different expected outputs).
Also checks that coding4a's --suite view + QuixBugs testcase parser
round-trip the suite unchanged, so the 4a grader sees exactly these cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.coding4a import build_suite_view, grade, list_programs, load_cases

BUGSUITE = Path(__file__).resolve().parent.parent / "bugsuite"

CATEGORIES = {
    "pagination", "interval-arithmetic", "index-calculation",
    "date-handling", "rounding",
}
BUG_TYPES = {
    "off-by-one", "comparison-operator", "wrong-rounding-mode",
    "inclusive-exclusive-end", "leap-year-edge", "plus-minus-one",
    "spec-contradiction",
}
SOURCES = {"handwritten", "livecodebench"}


def _tasks() -> list[Path]:
    dirs = sorted(p.parent for p in BUGSUITE.glob("*/meta.json"))
    assert dirs, f"no tasks found under {BUGSUITE}"
    return dirs


def _meta(task_dir: Path) -> dict:
    return json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))


def _cases(task_dir: Path) -> list[tuple[list, object]]:
    lines = (task_dir / "cases.json").read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if line.strip():
            args, expected = json.loads(line)
            assert isinstance(args, list)
            out.append((args, expected))
    return out


ALL = _tasks()
FEASIBLE = [d for d in ALL if _meta(d)["feasible"]]
INFEASIBLE = [d for d in ALL if not _meta(d)["feasible"]]
_ids = lambda dirs: [d.name for d in dirs]  # noqa: E731


# ---------------------------------------------------------------------------
# Suite-level shape
# ---------------------------------------------------------------------------


def test_suite_composition():
    assert len(FEASIBLE) == 30
    assert len(INFEASIBLE) == 5
    per_cat: dict[str, int] = {}
    for d in FEASIBLE:
        per_cat[_meta(d)["category"]] = per_cat.get(_meta(d)["category"], 0) + 1
    assert per_cat == {cat: 6 for cat in CATEGORIES}
    entries = [_meta(d)["entry_point"] for d in ALL]
    assert len(set(entries)) == len(entries), "entry_point collision"


def test_suite_view_matches_cases(tmp_path: Path):
    """coding4a --suite must grade exactly the cases stored in cases.json:
    the QuixBugs arity-disambiguation heuristic must not reinterpret any."""
    names = build_suite_view(BUGSUITE, tmp_path)
    assert sorted(names) == sorted(_meta(d)["entry_point"] for d in FEASIBLE)
    assert list_programs(tmp_path) == sorted(names)
    by_entry = {_meta(d)["entry_point"]: d for d in FEASIBLE}
    for name in names:
        parsed = load_cases(tmp_path, name)
        assert parsed == _cases(by_entry[name]), f"case drift for {name}"


# ---------------------------------------------------------------------------
# Per-task checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_dir", ALL, ids=_ids(ALL))
def test_meta_well_formed(task_dir: Path):
    meta = _meta(task_dir)
    assert meta["name"] == task_dir.name
    assert meta["category"] in CATEGORIES
    assert meta["bug_type"] in BUG_TYPES
    assert meta["source"] in SOURCES
    assert isinstance(meta["feasible"], bool)
    assert isinstance(meta["entry_point"], str) and meta["entry_point"]
    if meta["source"] == "livecodebench":
        assert meta["lcb_problem_id"]
    assert (task_dir / "buggy.py").exists()
    text = (task_dir / "buggy.py").read_text(encoding="utf-8")
    assert text.startswith('"""'), "buggy.py must open with the spec docstring"
    assert f"def {meta['entry_point']}(" in text
    if meta["feasible"]:
        assert (task_dir / "correct.py").exists()
        assert len(_cases(task_dir)) >= 8
        assert meta["bug_type"] != "spec-contradiction"
    else:
        assert task_dir.name.startswith("infeasible-")
        assert (task_dir / "spec.md").exists()
        assert not (task_dir / "correct.py").exists()


@pytest.mark.parametrize("task_dir", FEASIBLE, ids=_ids(FEASIBLE))
def test_correct_passes_all_cases(task_dir: Path):
    meta = _meta(task_dir)
    result = grade(task_dir / "correct.py", meta["entry_point"], _cases(task_dir))
    assert result["passed"], (
        f"reference implementation fails cases {result['cases_failed']}"
    )


@pytest.mark.parametrize("task_dir", FEASIBLE, ids=_ids(FEASIBLE))
def test_buggy_fails_some_case(task_dir: Path):
    meta = _meta(task_dir)
    result = grade(task_dir / "buggy.py", meta["entry_point"], _cases(task_dir))
    assert not result["passed"], "planted bug is not exposed by any case"
    # ... but buggy.py must still be a loadable, runnable implementation:
    # at most all-but-one case may fail only if the module itself works.
    assert result["n_cases"] == len(_cases(task_dir))


@pytest.mark.parametrize("task_dir", INFEASIBLE, ids=_ids(INFEASIBLE))
def test_infeasible_cases_unsatisfiable(task_dir: Path):
    """The contradiction must be encoded in the data itself: some input
    occurs twice with different expected outputs, so NO implementation can
    pass all cases."""
    seen: dict[str, str] = {}
    conflict = False
    for args, expected in _cases(task_dir):
        key = json.dumps(args)
        val = json.dumps(expected)
        if key in seen and seen[key] != val:
            conflict = True
        seen.setdefault(key, val)
    assert conflict, "no duplicated input with conflicting expected output"
