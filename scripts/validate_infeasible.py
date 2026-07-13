"""Mechanical validation of every bugsuite/infeasible-* task (実験4c 素材).

For each infeasible task directory it checks:

1. buggy.py imports cleanly and meta.json's entry_point is a callable in it.
2. every line of cases.json parses as a `[args_list, expected]` JSON pair.
3. the case set is provably UNSATISFIABLE: at least one input appears twice
   with two different expected outputs (the mechanical guarantee that no
   implementation — hence no agent fix — can ever pass all cases).
4. buggy.py is a plausible one-sided reading of the contradictory spec:
   run against the cases it never crashes, passes at least one case, and
   (by 3, necessarily — but checked directly) fails at least one case.

Comparison semantics mirror experiments/coding4a's subprocess grader
(bool/int distinction, isclose for numerics, elementwise for sequences).

Usage:
    uv run python scripts/validate_infeasible.py [suite_dir]

Exit status is non-zero if any task fails validation.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

DEFAULT_SUITE = Path(__file__).resolve().parent.parent / "bugsuite"


def close(a: object, b: object) -> bool:
    """Same equality as coding4a's _GRADER (with its default 1e-9 abs tol)."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b


def load_callable(task_dir: Path, entry_point: str):
    spec = importlib.util.spec_from_file_location(
        f"infeasible_{task_dir.name.replace('-', '_')}", task_dir / "buggy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, entry_point)
    if not callable(fn):
        raise TypeError(f"{entry_point} is not callable")
    return fn


def parse_cases(task_dir: Path) -> list[tuple[list, object]]:
    cases = []
    for i, line in enumerate(
        (task_dir / "cases.json").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        args, expected = json.loads(line)
        if not isinstance(args, list):
            raise ValueError(f"line {i}: args is not a list")
        cases.append((args, expected))
    return cases


def conflicting_pairs(cases: list[tuple[list, object]]) -> int:
    """Number of inputs that appear with >= 2 distinct expected outputs."""
    by_args: dict[str, set[str]] = {}
    for args, expected in cases:
        by_args.setdefault(json.dumps(args), set()).add(json.dumps(expected))
    return sum(1 for vals in by_args.values() if len(vals) > 1)


def validate(task_dir: Path) -> tuple[bool, str]:
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))

    fn = load_callable(task_dir, meta["entry_point"])          # check 1
    cases = parse_cases(task_dir)                              # check 2

    pairs = conflicting_pairs(cases)                           # check 3
    if pairs < 1:
        return False, "no duplicated input with conflicting expected output"

    passed = failed = 0                                        # check 4
    for args, expected in cases:
        try:
            out = fn(*args)
        except Exception as exc:  # noqa: BLE001 — any crash fails validation
            return False, f"buggy.py crashed on {args!r}: {exc!r}"
        if close(out, expected):
            passed += 1
        else:
            failed += 1
    if passed == 0:
        return False, "buggy.py passes NO case (not a plausible reading)"
    if failed == 0:
        return False, "buggy.py passes ALL cases (task would be feasible)"

    tier = meta.get("tier", "-")
    return True, (
        f"tier={tier:<16} cases={len(cases):>2} "
        f"conflict_pairs={pairs} pass={passed} fail={failed}"
    )


def main() -> int:
    suite = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SUITE
    task_dirs = sorted(
        p for p in suite.glob("infeasible-*") if (p / "meta.json").exists()
    )
    if not task_dirs:
        print(f"no infeasible-* tasks under {suite}", file=sys.stderr)
        return 2

    failures = 0
    for d in task_dirs:
        try:
            ok, detail = validate(d)
        except Exception as exc:  # noqa: BLE001 — report, keep validating
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{mark}] {d.name:<32} {detail}")
    print(f"\n{len(task_dirs) - failures}/{len(task_dirs)} tasks valid")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
