"""Experiment 4c runner — completion judgment: self-declared vs loop-judge.

Conditions (docs/experiment-plan.md §実験4c):

- self   the agent iterates on a fix task and decides on its own when it is
         done ("もう大丈夫そう") or that the task is impossible
- judge  same loop, but the agent must consult the loop-judge subagent and
         report its DONE/CONTINUE/STALLED verdict before it may declare

Tasks come from a bugsuite-format directory (buggy.py + hidden cases.json +
meta.json with entry_point/feasible, optional spec.md). Twenty tasks (5
original + 15 tiered extensions, see bugsuite/README.md) are deliberately
infeasible (contradictory spec): the correct behaviour there is to declare
STALLED rather than loop forever or claim success.

Protocol: one SDK session per task. Each query/response exchange is one
iteration. A response ending with `FINAL: DONE` or `FINAL: STALLED` ends the
loop; otherwise the harness replies with a fixed continuation prompt up to
--max-iters. The declaration is then graded against the HIDDEN testcases
(never present in the workdir):

- early_completion  declared DONE but hidden cases fail (the paper's
                    "早すぎる完了宣言" metric)
- stalled_detected  infeasible task correctly declared STALLED
- diverged          infeasible task never declared (hit --max-iters)

Deviation from nsai's stock loop-judge: its tool list includes Bash (to run
project tests). This experiment runs hermetically without Bash — the judge
inspects the code via Read and discharges recorded predicates via
kb_verify/smt_verify instead. Grading is SDK-free (lazy imports) so tests can
exercise task loading, FINAL parsing, and metrics offline.

Usage:
    uv run python -m experiments.coding4c --suite bugsuite --condition judge \
        --model sonnet --out results/coding4c-judge.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from experiments.coding4a import grade

CONDITIONS = ("self", "judge")

# Same pitfall as coding4a: with permission_mode="dontAsk" the built-in tool
# universe must be limited via tools=[...], not allowed_tools.
FILE_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"]

_FINAL_RE = re.compile(r"FINAL:\s*(DONE|STALLED)\b", re.IGNORECASE)

BASE_SYSTEM = (
    "You are a careful senior software engineer working on a small Python fix "
    "task. The workspace contains the implementation (buggy.py) and, if "
    "present, a spec (spec.md). The task is complete only when the "
    "implementation satisfies the whole stated contract, including boundary "
    "cases. Work iteratively; you will be told to continue until you declare. "
    "Declare by ending a reply with exactly one line:\n"
    "FINAL: DONE     — you are confident the task is complete\n"
    "FINAL: STALLED  — the requirements are contradictory or unsatisfiable "
    "and no implementation can meet them\n"
    "Do not declare DONE on hope; do not declare STALLED merely because the "
    "task is hard."
)

SELF_SYSTEM = BASE_SYSTEM + (
    " You have file tools and symbolic tools (smt_verify, kb_*). Judge "
    "completion yourself."
)

JUDGE_SYSTEM = BASE_SYSTEM + (
    " You have file tools, symbolic tools (smt_verify, kb_*), and a "
    "'loop-judge' subagent (Task tool). Before ANY FINAL declaration you must "
    "consult loop-judge synchronously (run_in_background: false), wait for "
    "its DONE/CONTINUE/STALLED verdict, and report that verdict verbatim in "
    "your reply. Follow the verdict: only declare FINAL: DONE after a DONE "
    "verdict and FINAL: STALLED after a STALLED verdict; on CONTINUE, keep "
    "working."
)

TASK_PROMPT = (
    "Fix task: buggy.py contains a small defect relative to its contract "
    "({spec_source}). Make the implementation satisfy the full contract with "
    "a minimal edit. Iterate as needed; declare with a FINAL line when you "
    "are done or the task is impossible."
)

CONTINUE_PROMPT = (
    "You have not declared completion. Continue working. When the contract "
    "is fully satisfied reply with FINAL: DONE; if you conclude the "
    "requirements are contradictory reply with FINAL: STALLED."
)


# ---------------------------------------------------------------------------
# Task loading (bugsuite format — see bugsuite/README.md)
# ---------------------------------------------------------------------------


def load_suite(suite: Path) -> list[dict]:
    """Every task dir with buggy.py + cases.json + meta.json, sorted by name."""
    tasks = []
    for meta_file in sorted(suite.glob("*/meta.json")):
        d = meta_file.parent
        if not (d / "buggy.py").exists() or not (d / "cases.json").exists():
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        tasks.append(
            {
                "id": meta["name"],
                "dir": d,
                "entry_point": meta["entry_point"],
                "feasible": bool(meta.get("feasible", True)),
            }
        )
    return tasks


def load_cases(task: dict) -> list[tuple[list, object]]:
    lines = (task["dir"] / "cases.json").read_text(encoding="utf-8").splitlines()
    return [tuple(json.loads(line)) for line in lines if line.strip()]


def parse_declaration(text: str) -> str | None:
    """Last FINAL declaration in one response's text: 'done' | 'stalled' | None."""
    matches = _FINAL_RE.findall(text)
    return matches[-1].lower() if matches else None


def judge_metrics(feasible: bool, declared: str | None, hidden_passed: bool) -> dict:
    return {
        "early_completion": declared == "done" and not hidden_passed,
        "stalled_detected": (not feasible) and declared == "stalled",
        "diverged": (not feasible) and declared is None,
    }


# ---------------------------------------------------------------------------
# Agent runner (SDK imported lazily)
# ---------------------------------------------------------------------------


def build_task_options(condition: str, model: str | None, workdir: Path, kb_path: Path):
    from claude_agent_sdk import ClaudeAgentOptions

    from nsai.kb import KnowledgeBase
    from nsai.subagents import LOOP_JUDGE_TOOLS, build_agents
    from nsai.tools import ALLOWED_TOOL_NAMES, build_server

    kb = KnowledgeBase(kb_path)  # throwaway scratch KB, one per task
    common = dict(
        model=model,
        cwd=str(workdir),
        mcp_servers={"symbolic": build_server(kb)},
        permission_mode="dontAsk",
        setting_sources=[],
    )
    if condition == "self":
        return ClaudeAgentOptions(
            system_prompt=SELF_SYSTEM,
            tools=list(FILE_TOOLS),
            allowed_tools=list(FILE_TOOLS) + list(ALLOWED_TOOL_NAMES),
            **common,
        )
    judge = build_agents(coding=True)["loop-judge"]
    # Hermetic run: no Bash anywhere (see module docstring), judge reads code.
    judge.tools = [t for t in LOOP_JUDGE_TOOLS if t != "Bash"] + ["Read"]
    return ClaudeAgentOptions(
        system_prompt=JUDGE_SYSTEM,
        tools=list(FILE_TOOLS) + ["Task"],
        allowed_tools=list(FILE_TOOLS) + ["Task"] + list(ALLOWED_TOOL_NAMES),
        agents={"loop-judge": judge},
        **common,
    )


async def run_task(
    task: dict, condition: str, model: str | None, max_iters: int,
) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        ResultMessage,
        ToolUseBlock,
    )

    cases = load_cases(task)
    tool_calls: dict[str, int] = {}
    cost = None
    declared: str | None = None
    iterations = 0
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        shutil.copy(task["dir"] / "buggy.py", workdir / "buggy.py")
        spec = task["dir"] / "spec.md"
        spec_source = "its top docstring"
        if spec.exists():
            shutil.copy(spec, workdir / "spec.md")
            spec_source = "spec.md and its top docstring"
        options = build_task_options(condition, model, workdir, Path(tmp) / "kb.ttl")

        async with ClaudeSDKClient(options=options) as client:
            prompt = TASK_PROMPT.format(spec_source=spec_source)
            while iterations < max_iters and declared is None:
                iterations += 1
                await client.query(prompt)
                text_parts: list[str] = []
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                tool_calls[block.name] = tool_calls.get(block.name, 0) + 1
                            elif hasattr(block, "text"):
                                text_parts.append(block.text)
                    elif isinstance(message, ResultMessage):
                        cost = message.total_cost_usd  # cumulative per session
                declared = parse_declaration("\n".join(text_parts))
                prompt = CONTINUE_PROMPT

        result = grade(workdir / "buggy.py", task["entry_point"], cases)
    record = {
        "task_id": task["id"],
        "task_type": "coding4c",
        "condition": condition,
        "model": model,
        "feasible": task["feasible"],
        "declared": declared,          # done | stalled | None (hit max_iters)
        "iterations": iterations,
        "hidden_passed": result["passed"],
        "n_cases": result["n_cases"],
        "cases_failed": result["cases_failed"],
        "cost_usd": cost,
        "seconds": round(time.monotonic() - start, 2),
        "tool_calls": tool_calls,
    }
    record.update(judge_metrics(task["feasible"], declared, result["passed"]))
    return record


async def run_all(
    tasks: list[dict], condition: str, model: str | None,
    runs: int, max_iters: int, out: Path,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        for i, task in enumerate(tasks):
            record = await run_task(task, condition, model, max_iters)
            record["run"] = run
            # Per-record append-open (same rationale as harness/coding4a).
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            mark = "+" if not record["early_completion"] and (
                record["hidden_passed"] or record["stalled_detected"]
            ) else "-"
            print(
                f"[{condition} run{run} {i + 1}/{len(tasks)}] {mark} {task['id']} "
                f"declared={record['declared']} iters={record['iterations']} "
                f"hidden_passed={record['hidden_passed']} "
                f"cost=${(record['cost_usd'] or 0):.4f}",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="Experiment 4c: self vs loop-judge completion")
    ap.add_argument("--suite", type=Path, required=True, help="bugsuite-format directory")
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--model", default=None, help="Model alias (e.g. sonnet) or full id")
    ap.add_argument("--tasks", default=None,
                    help="Comma-separated task names (default: all in the suite)")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N tasks")
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True, help="Results JSONL (appended)")
    args = ap.parse_args()

    tasks = load_suite(args.suite)
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        missing = wanted - {t["id"] for t in tasks}
        if missing:
            ap.error(f"unknown tasks: {', '.join(sorted(missing))}")
        tasks = [t for t in tasks if t["id"] in wanted]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        ap.error(f"no runnable tasks found under {args.suite}")

    asyncio.run(
        run_all(tasks, args.condition, args.model, args.runs, args.max_iters, args.out)
    )


if __name__ == "__main__":
    main()
