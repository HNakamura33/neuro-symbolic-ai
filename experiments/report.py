"""Aggregate harness result files into a markdown comparison report.

Usage:
    uv run python -m experiments.report results/dev-*.jsonl
    uv run python -m experiments.report results/*.jsonl --compare C1 C2
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .grade import load_records, mcnemar_exact, summarize


def _fmt(x, digits: int = 3) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def build_report(paths: list[Path], compare: tuple[str, str] | None = None) -> str:
    records = [r for p in paths for r in load_records(p)]
    by_condition: dict[str, list[dict]] = {}
    for r in records:
        by_condition.setdefault(r["condition"], []).append(r)

    lines = ["# Experiment report", ""]
    lines += ["| condition | n | accuracy | macro-F1 | false-verif. | cost $ | sec | unparsed |",
              "|---|---|---|---|---|---|---|---|"]
    for cond in sorted(by_condition):
        s = summarize(by_condition[cond])
        tw = s.get("three_way") or {}
        lines.append(
            f"| {cond} | {s['n']} | {_fmt(s['accuracy'])} | {_fmt(tw.get('macro_f1'))} "
            f"| {_fmt(tw.get('false_verification_rate'))} | {_fmt(s['mean_cost_usd'], 4)} "
            f"| {_fmt(s['mean_seconds'], 1)} | {s['unparsed']} |"
        )

    hop_rows = {c: summarize(rs).get("accuracy_by_hops") for c, rs in sorted(by_condition.items())}
    if any(hop_rows.values()):
        hops = sorted({h for v in hop_rows.values() if v for h in v})
        lines += ["", "## Accuracy by hops", "",
                  "| condition | " + " | ".join(f"{h}-hop" for h in hops) + " |",
                  "|---|" + "---|" * len(hops)]
        for cond, by_hops in hop_rows.items():
            if by_hops:
                lines.append(f"| {cond} | " + " | ".join(_fmt(by_hops.get(h)) for h in hops) + " |")

    if compare:
        a, b = compare
        if a in by_condition and b in by_condition:
            m = mcnemar_exact(by_condition[a], by_condition[b])
            lines += ["", f"## McNemar {a} vs {b}", "",
                      f"discordant pairs: {a}-only-correct={m['b']}, {b}-only-correct={m['c']}, "
                      f"p={m['p_value']:.4f}"]

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize experiment results as markdown")
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--compare", nargs=2, metavar=("COND_A", "COND_B"),
                    help="Also run McNemar between two conditions")
    ap.add_argument("--out", type=Path, default=None, help="Write to file instead of stdout")
    args = ap.parse_args()
    report = build_report(args.results, tuple(args.compare) if args.compare else None)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
