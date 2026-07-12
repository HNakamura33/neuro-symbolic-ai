"""Build the Lane-F rerun subsets from a graded MetaQA results file.

Two datasets, both with the resume-C2f-null layout
(qa.jsonl + kb.ttl + entities.json + meta.json):

- <out-failed>:  the qa tasks the given condition got WRONG (correct: false,
  one run) — measures the recovery rate of S1 kb_check_answer + S4
  verify-before-FINAL on the known failure set.
- <out-control>: an equal-sized seeded random sample of the tasks it got
  RIGHT — the non-regression control group for the same change.

kb.ttl and entities.json are copied unchanged from the source dataset;
meta.json is the source meta plus provenance fields for the subset.

Usage (paths may point at another worktree; nothing here touches git):
    uv run python scripts/make_resume_c1_sets.py \
        --results results/metaqa-C1.jsonl --dataset data/metaqa \
        --out-failed data/resume-C1-failed --out-control data/resume-C1-control
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import date
from pathlib import Path


def split_task_ids(results: Path, run: int) -> tuple[list[str], list[str]]:
    """(failed_ids, passed_ids) for qa records of one run, in file order."""
    failed: list[str] = []
    passed: list[str] = []
    for line in results.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("task_type") != "qa" or rec.get("run") != run:
            continue
        (passed if rec.get("correct") else failed).append(rec["task_id"])
    return failed, passed


def write_subset(
    dataset: Path, out: Path, ids: list[str], results: Path, criteria: str,
    seed: int | None = None, force: bool = False,
) -> list[str]:
    """Filter dataset/qa.jsonl to `ids` (source order kept) and write the subset dir."""
    if out.exists() and any(out.iterdir()) and not force:
        raise SystemExit(f"refusing to overwrite non-empty {out} (use --force)")
    wanted = set(ids)
    tasks = [
        json.loads(line)
        for line in (dataset / "qa.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    keep = [t for t in tasks if t["id"] in wanted]
    missing = wanted - {t["id"] for t in keep}
    if missing:
        raise SystemExit(f"{len(missing)} task_ids not found in {dataset}/qa.jsonl: "
                         f"{sorted(missing)[:5]}...")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "qa.jsonl").open("w", encoding="utf-8") as f:
        for t in keep:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    for name in ("kb.ttl", "entities.json"):
        shutil.copy2(dataset / name, out / name)
    meta = json.loads((dataset / "meta.json").read_text(encoding="utf-8"))
    meta["qa_written"] = len(keep)
    meta["subset"] = criteria
    meta["subset_results"] = str(results)
    if seed is not None:
        meta["subset_seed"] = seed
    meta["subset_created"] = date.today().isoformat()
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return [t["id"] for t in keep]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--out-failed", type=Path, required=True)
    ap.add_argument("--out-control", type=Path, required=True)
    ap.add_argument("--run", type=int, default=0, help="which run's grades to use")
    ap.add_argument("--seed", type=int, default=42, help="control-sample seed")
    ap.add_argument("--force", action="store_true", help="overwrite existing output dirs")
    args = ap.parse_args()

    failed, passed = split_task_ids(args.results, args.run)
    if not failed:
        raise SystemExit(f"no failed qa tasks in {args.results} (run {args.run})")
    control = random.Random(args.seed).sample(passed, len(failed))

    written_failed = write_subset(
        args.dataset, args.out_failed, failed, args.results,
        f"tasks with correct: false in run {args.run} — "
        "S1+S4 (kb_check_answer + verify-before-FINAL) recovery rerun",
        force=args.force,
    )
    written_control = write_subset(
        args.dataset, args.out_control, control, args.results,
        f"seed-{args.seed} random sample of correct: true tasks in run {args.run}, "
        "size-matched to the failed subset — non-regression control for S1+S4",
        seed=args.seed, force=args.force,
    )

    # Verification: counts and exact task_id agreement with the results file.
    assert sorted(written_failed) == sorted(failed), "failed subset id mismatch"
    assert sorted(written_control) == sorted(control), "control subset id mismatch"
    assert not set(written_failed) & set(written_control), "subsets overlap"
    print(f"results: {args.results} (run {args.run}): "
          f"{len(failed)} failed / {len(passed)} passed")
    print(f"{args.out_failed}: {len(written_failed)} tasks (ids match results: True)")
    print(f"{args.out_control}: {len(written_control)} tasks "
          f"(ids drawn from correct set, seed {args.seed}, disjoint from failed: True)")


if __name__ == "__main__":
    main()
