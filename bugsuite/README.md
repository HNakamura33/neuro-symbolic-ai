# bugsuite — hand-crafted boundary-bug suite (実験4a 自作分 / 実験4c 素材)

30 feasible single-defect bug-fixing tasks (6 per category) plus 5
deliberately INFEASIBLE variants with self-contradictory specs for the
loop-judge experiment (実験4c).

## Layout

Each task lives in `bugsuite/<name>/`:

- `buggy.py` — the single-defect implementation. This is the ONLY file an
  agent under test may see. The top-of-file docstring describes the function
  contract (it is the spec).
- `cases.json` — hidden testcases, one JSON line per case:
  `[args_list, expected]` (QuixBugs `json_testcases` format, so
  `experiments/coding4a.py`'s grader works unchanged). Every feasible task
  has >= 8 cases, all passing against the reference implementation, and
  includes the boundary cases that expose the planted bug.
- `correct.py` — the correct reference implementation. Hidden from agents;
  used only for mechanical validation (`tests/test_bugsuite.py`) and as 実験4c
  material. Identical to `buggy.py` except for the single planted defect.
- `meta.json` — `{"name", "category", "bug_type", "source", "lcb_problem_id"
  (livecodebench tasks only), "feasible", "entry_point"}`.
- `spec.md` — infeasible variants only: the full (self-contradictory)
  requirements document.

Infeasible variants live in `bugsuite/infeasible-<name>/` with
`"feasible": false`, no `correct.py` (none can exist), and a `cases.json`
that is itself unsatisfiable: it contains the same input twice with two
different expected outputs, mirroring the contradiction in `spec.md`.

## Categories (6 feasible tasks each)

`pagination`, `interval-arithmetic`, `index-calculation`, `date-handling`,
`rounding`.

## Bug types

Exactly one boundary-class defect is planted per feasible task, tagged in
`meta.json` as one of:

- `off-by-one` — loop/window bound short or long by one
- `comparison-operator` — `<` ↔ `<=` (or `>` ↔ `>=`)
- `wrong-rounding-mode` — floor/ceil/banker's/half-up confusion
- `inclusive-exclusive-end` — closed vs half-open end treatment
- `leap-year-edge` — missing century/400-year rule
- `plus-minus-one` — a `±1` constant dropped or added
- `spec-contradiction` — infeasible variants only

## Sources

- `handwritten` (19 tasks): authored for this suite.
- `livecodebench` (11 tasks): contamination-free half. Correct solutions to
  LiveCodeBench `code_generation_lite` release-v6 problems (contest dates
  2025-01 .. 2025-04, post-cutoff), each validated against the FULL LCB
  public+private test suite before the bug was planted. `lcb_problem_id`
  records the LCB `question_id`; a few small original LCB testcases are
  folded into `cases.json` alongside the custom boundary cases.

## Running

Experiment 4a on this suite (same runner and grader as QuixBugs mode):

```sh
uv run python -m experiments.coding4a --condition plain --model sonnet \
    --suite bugsuite --out results/coding4a-bugsuite-plain.jsonl
```

`--suite` materialises a QuixBugs-layout view (programs named by
`entry_point`) in a temp dir; infeasible variants are excluded automatically.

Mechanical validation (offline):

```sh
uv run pytest -q tests/test_bugsuite.py
```

It asserts, for every feasible task, that `correct.py` passes ALL cases and
`buggy.py` fails at least one, and for every infeasible variant that
`cases.json` is unsatisfiable.
