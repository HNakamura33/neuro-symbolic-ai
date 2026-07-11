"""Killability oracle for experiment 4b — normalize out equivalent mutants.

The 4b smoke runs saturated: every model x condition cell reached the same
mutation score with the identical survivor set, so the raw score's ceiling is
set by *equivalent mutants*, not by test quality. This module uses the
EvalPlus plus-tests (base_input + plus_input from the release files) as a
killability oracle: a mutant is KILLABLE iff some oracle input makes it
behave differently from the canonical solution (different value, exception,
or timeout). Mutants no oracle input can distinguish are treated as
equivalent and dropped from the denominator:

    mutation_score_norm = killed-and-killable / killable

The oracle is a *lower bound* on killability (plus-tests are extensive but
not exhaustive), so normalized scores are an upper-bound estimate; the paper
reports both raw and normalized.

Everything here is offline (stdlib subprocesses, no LLM, no SDK). Each
mutant is judged in its own subprocess: the driver runs canonical and mutant
on each input (deep-copied — functions may mutate their arguments) with a
per-call SIGALRM timeout, early-exiting at the first difference. Inputs on
which the canonical solution itself errors or times out carry no signal and
are skipped. A subprocess that dies or exceeds the outer backstop timeout is
recorded killable=None (unknown) and counted as killable-but-unkilled — the
conservative direction, it can only lower normalized scores.

Killability depends only on (task, mutant list, inputs), all deterministic,
so verdicts are cached as JSON under the EvalPlus cache dir and reused across
rescoring runs.

Usage (rescore existing result JSONLs, print a summary table):
    uv run python -m experiments.oracle4b --dataset humaneval \
        --results results/smoke-4b-he39-*.jsonl \
        --out results/smoke-4b-he39-norm.jsonl
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

from experiments.coding4b import DEFAULT_CACHE, fetch_evalplus, load_evalplus
from experiments.mutate import mutants

# Runs in a fresh subprocess: judge ONE mutant against the oracle inputs.
# stdin: {"correct", "mutant", "entry_point", "inputs", "atol", "per_call_timeout"}
# stdout: {"killable": bool, "input_index"?, "detail"?}
_DRIVER = r"""
import json, math, signal, sys
from copy import deepcopy

def eq(a, b, atol):
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=atol or 1e-9)
    if type(a) is not type(b):
        return False
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(eq(x, y, atol) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(eq(v, b[k], atol) for k, v in a.items())
    try:
        return bool(a == b)
    except Exception:
        return False

class _Timeout(Exception):
    pass

def _handler(signum, frame):
    raise _Timeout()

def call(fn, args, seconds):
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return "ok", fn(*deepcopy(args))
    except _Timeout:
        return "timeout", None
    except BaseException as exc:
        return type(exc).__name__, None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

def main():
    payload = json.load(sys.stdin)
    ns_correct, ns_mutant = {}, {}
    exec(payload["correct"], ns_correct)
    exec(payload["mutant"], ns_mutant)
    f_correct = ns_correct[payload["entry_point"]]
    f_mutant = ns_mutant[payload["entry_point"]]
    atol = payload["atol"]
    seconds = payload["per_call_timeout"]
    signal.signal(signal.SIGALRM, _handler)
    for i, args in enumerate(payload["inputs"]):
        status_c, value_c = call(f_correct, args, seconds)
        if status_c != "ok":
            continue  # canonical misbehaves here: the input carries no signal
        status_m, value_m = call(f_mutant, args, seconds)
        if status_m != "ok" or not eq(value_c, value_m, atol):
            detail = status_m if status_m != "ok" else "value"
            print(json.dumps({"killable": True, "input_index": i, "detail": detail}))
            return
    print(json.dumps({"killable": False}))

main()
"""


def load_inputs(dataset: str, cache_dir: Path) -> dict[str, dict]:
    """Oracle inputs per normalized task id: {"inputs": base+plus, "atol"}."""
    out: dict[str, dict] = {}
    with gzip.open(fetch_evalplus(dataset, cache_dir), "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            out[row["task_id"].replace("/", "-")] = {
                "inputs": list(row["base_input"]) + list(row["plus_input"]),
                "atol": row.get("atol") or 0,
            }
    return out


def judge_mutant(
    correct_source: str, mutant_source: str, entry_point: str,
    inputs: list, atol: float, per_call_timeout: float, outer_timeout: float,
) -> bool | None:
    """True = killable, False = equivalent under the oracle, None = the
    driver itself died or timed out (unknown; callers count it as killable
    to stay conservative)."""
    payload = json.dumps({
        "correct": correct_source,
        "mutant": mutant_source,
        "entry_point": entry_point,
        "inputs": inputs,
        "atol": atol,
        "per_call_timeout": per_call_timeout,
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _DRIVER],
            input=payload, capture_output=True, text=True, timeout=outer_timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return bool(json.loads(proc.stdout)["killable"])


def killability(
    task: dict, oracle: dict, per_call_timeout: float = 5.0,
    outer_timeout: float = 300.0,
) -> list[bool | None]:
    """Oracle verdict per mutant, in mutants()' order (= kill_matrix order)."""
    muts = mutants(task["correct_source"], task["entry_point"])
    return [
        judge_mutant(
            task["correct_source"], m.source, task["entry_point"],
            oracle["inputs"], oracle["atol"], per_call_timeout, outer_timeout,
        )
        for m in muts
    ]


# ---------------------------------------------------------------------------
# Verdict cache (killability is deterministic per task)
# ---------------------------------------------------------------------------


def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def cached_killability(
    task: dict, oracle: dict, cache: dict, per_call_timeout: float,
    outer_timeout: float,
) -> list[bool | None]:
    """Cache entries are keyed by task id and validated against the mutant
    count — a changed mutation engine invalidates them automatically."""
    n = len(mutants(task["correct_source"], task["entry_point"]))
    entry = cache.get(task["task_id"])
    if entry and entry["n_mutants"] == n:
        return entry["killable"]
    verdicts = killability(task, oracle, per_call_timeout, outer_timeout)
    cache[task["task_id"]] = {"n_mutants": n, "killable": verdicts}
    return verdicts


# ---------------------------------------------------------------------------
# Rescoring
# ---------------------------------------------------------------------------


def rescore(record: dict, verdicts: list[bool | None], boundary_mask: list[bool]) -> dict:
    """Normalized scores for one result record. None verdicts (driver died)
    count as killable-but-whatever-the-suite-did — conservative."""
    kill = record["kill_matrix"]
    if len(kill) != len(verdicts):
        raise ValueError(
            f"{record['task_id']}: kill_matrix has {len(kill)} entries "
            f"but the oracle judged {len(verdicts)} mutants"
        )
    killable = [v is not False for v in verdicts]
    n_killable = sum(killable)
    n_killable_boundary = sum(k for k, b in zip(killable, boundary_mask) if b)
    killed = sum(k for k, ka in zip(kill, killable) if ka)
    killed_boundary = sum(
        k for k, ka, b in zip(kill, killable, boundary_mask) if ka and b
    )
    return {
        **record,
        "n_killable_mutants": n_killable,
        "n_equivalent_mutants": len(kill) - n_killable,
        "n_oracle_unknown": sum(v is None for v in verdicts),
        "mutation_score_norm": (killed / n_killable) if n_killable else None,
        "boundary_score_norm": (
            (killed_boundary / n_killable_boundary) if n_killable_boundary else None
        ),
    }


def summarize(records: list[dict]) -> str:
    """Per (model, condition): validity rate, then raw vs normalized means
    over VALID suites only (the main-run reporting design)."""
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault((str(r.get("model")), r["condition"]), []).append(r)

    def mean(values: list) -> str:
        values = [v for v in values if v is not None]
        return f"{sum(values) / len(values):.3f}" if values else "-"

    header = (
        f"{'model':>8} {'condition':>13} {'n':>3} {'valid':>6} "
        f"{'raw':>6} {'norm':>6} {'b.raw':>6} {'b.norm':>6}"
    )
    lines = [header, "-" * len(header)]
    for (model, condition), rs in sorted(groups.items()):
        valid = [r for r in rs if r.get("tests_pass_original")]
        lines.append(
            f"{model:>8} {condition:>13} {len(rs):>3} "
            f"{len(valid) / len(rs):>6.2f} "
            f"{mean([r['mutation_score'] for r in valid]):>6} "
            f"{mean([r['mutation_score_norm'] for r in valid]):>6} "
            f"{mean([r['boundary_score'] for r in valid]):>6} "
            f"{mean([r['boundary_score_norm'] for r in valid]):>6}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="4b killability oracle: rescore results with equivalent "
                    "mutants removed from the denominator"
    )
    ap.add_argument("--results", type=Path, nargs="+", required=True,
                    help="Result JSONLs from experiments.coding4b (evalplus source)")
    ap.add_argument("--dataset", choices=("humaneval", "mbpp"), default="humaneval")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                    help="EvalPlus cache dir (also holds the verdict cache)")
    ap.add_argument("--per-call-timeout", type=float, default=5.0)
    ap.add_argument("--outer-timeout", type=float, default=300.0,
                    help="Backstop per-mutant subprocess timeout")
    ap.add_argument("--out", type=Path, default=None,
                    help="Write rescored records here (JSONL, overwritten)")
    args = ap.parse_args()

    tasks = {t["task_id"]: t for t in load_evalplus(args.dataset, args.cache)}
    oracle_inputs = load_inputs(args.dataset, args.cache)
    cache_path = args.cache / f"killable-{args.dataset}.json"
    cache = load_cache(cache_path)

    records = []
    for path in args.results:
        with path.open(encoding="utf-8") as f:
            records.extend(json.loads(line) for line in f if line.strip())

    rescored = []
    for record in records:
        task_id = record["task_id"]
        if task_id not in tasks:
            raise SystemExit(
                f"{task_id}: not in EvalPlus {args.dataset} — the oracle only "
                "applies to --source evalplus results"
            )
        task = tasks[task_id]
        verdicts = cached_killability(
            task, oracle_inputs[task_id], cache,
            args.per_call_timeout, args.outer_timeout,
        )
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        boundary_mask = [
            m.boundary for m in mutants(task["correct_source"], task["entry_point"])
        ]
        rescored.append(rescore(record, verdicts, boundary_mask))
        r = rescored[-1]
        print(
            f"[{task_id} {r['condition']} {r.get('model')}] "
            f"killable={r['n_killable_mutants']}/{r['n_mutants']} "
            f"(equiv={r['n_equivalent_mutants']}, unknown={r['n_oracle_unknown']}) "
            f"raw={r['mutation_score']} -> norm="
            f"{r['mutation_score_norm'] if r['mutation_score_norm'] is None else round(r['mutation_score_norm'], 3)}",
            flush=True,
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for r in rescored:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print(summarize(rescored))


if __name__ == "__main__":
    main()
