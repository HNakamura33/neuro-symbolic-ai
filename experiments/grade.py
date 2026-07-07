"""Metrics and statistics for experiment results (pure functions, no LLM).

Consumes the JSONL records written by harness.py. Provides:

- accuracy / exact match
- 3-way verdict metrics: per-label precision/recall/F1, macro-F1, and the
  false-verification rate (the paper's operational definition of
  hallucination: gold is contradicted/unknown but the model said entailed)
- McNemar's exact test for paired binary outcomes (same tasks, two conditions)
- paired bootstrap for mean differences
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path

LABELS = ("entailed", "contradicted", "unknown")


def load_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def accuracy(records: list[dict]) -> float:
    return sum(r["correct"] for r in records) / len(records) if records else 0.0


def three_way_metrics(records: list[dict]) -> dict:
    """Per-label P/R/F1 + macro-F1 + false-verification rate for claim tasks."""
    per_label = {}
    f1s = []
    for label in LABELS:
        tp = sum(1 for r in records if r["gold"] == label and r["pred"] == label)
        fp = sum(1 for r in records if r["gold"] != label and r["pred"] == label)
        fn = sum(1 for r in records if r["gold"] == label and r["pred"] != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_label[label] = {"precision": prec, "recall": rec, "f1": f1}
        f1s.append(f1)
    not_entailed = [r for r in records if r["gold"] != "entailed"]
    false_verified = sum(1 for r in not_entailed if r["pred"] == "entailed")
    return {
        "per_label": per_label,
        "macro_f1": sum(f1s) / len(f1s),
        "false_verification_rate": false_verified / len(not_entailed) if not_entailed else 0.0,
        "n": len(records),
    }


def mcnemar_exact(records_a: list[dict], records_b: list[dict]) -> dict:
    """McNemar's exact test on paired correctness (matched by task_id + run).

    b = tasks A got right and B got wrong; c = the reverse. Two-sided exact
    binomial p-value on the discordant pairs.
    """
    by_key_b = {(r["task_id"], r.get("run", 0)): r["correct"] for r in records_b}
    b = c = 0
    for r in records_a:
        key = (r["task_id"], r.get("run", 0))
        if key not in by_key_b:
            continue
        if r["correct"] and not by_key_b[key]:
            b += 1
        elif not r["correct"] and by_key_b[key]:
            c += 1
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "p_value": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return {"b": b, "c": c, "p_value": min(1.0, 2 * tail)}


def paired_bootstrap(
    values_a: list[float], values_b: list[float], n_resamples: int = 10_000, seed: int = 0
) -> dict:
    """Two-sided bootstrap p-value + 95% CI for mean(a) - mean(b), paired."""
    assert len(values_a) == len(values_b) and values_a, "paired samples required"
    rng = random.Random(seed)
    diffs = [a - b for a, b in zip(values_a, values_b)]
    observed = sum(diffs) / len(diffs)
    means = []
    for _ in range(n_resamples):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * n_resamples)]
    hi = means[int(0.975 * n_resamples)]
    # Sign-flip p-value: how often a zero-centered resample is as extreme.
    centered = [m - observed for m in means]
    p = sum(1 for m in centered if abs(m + observed) >= abs(observed)) / n_resamples
    return {"mean_diff": observed, "ci95": (lo, hi), "p_value": min(1.0, p)}


def summarize(records: list[dict]) -> dict:
    """One condition's headline numbers (works for claims and qa records)."""
    out: dict = {
        "n": len(records),
        "accuracy": accuracy(records),
        "mean_cost_usd": _mean([r["cost_usd"] for r in records if r.get("cost_usd") is not None]),
        "mean_seconds": _mean([r["seconds"] for r in records if r.get("seconds") is not None]),
        "unparsed": sum(1 for r in records if r["pred"] is None),
    }
    if records and records[0]["task_type"] == "claims":
        out["three_way"] = three_way_metrics(records)
    if records and records[0]["task_type"] == "qa":
        by_hops: dict[int, list[dict]] = {}
        for r in records:
            by_hops.setdefault(r.get("hops"), []).append(r)
        out["accuracy_by_hops"] = {k: accuracy(v) for k, v in sorted(by_hops.items())}
    tool_totals: Counter = Counter()
    for r in records:
        tool_totals.update(r.get("tool_calls") or {})
    out["tool_calls_total"] = dict(tool_totals)
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None
