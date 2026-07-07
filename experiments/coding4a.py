"""Experiment 4a runner — QuixBugs bug-fixing: plain coding agent vs nsai code.

Conditions (docs/experiment-plan.md §実験4a):

- plain  file tools only (Read/Edit/Write/Glob/Grep) — the ablation baseline
- nsai   same file tools + the symbolic MCP server (smt_verify, csp_solve, ...)
         with a system-prompt instruction to check boundary conditions via SMT
         before declaring the fix done

Each task gets a fresh temp workdir containing ONLY the buggy program
(<name>.py). The correct implementation and the json testcases are hidden
evaluation data: they are never copied into the workdir. After the agent
finishes, the edited file is graded by running every json testcase in its own
subprocess with a per-case timeout (buggy QuixBugs programs can loop forever,
and so can bad fixes).

Grading is SDK-free: the Claude Agent SDK and nsai are imported lazily inside
the runner so tests can import this module offline.

Usage:
    uv run python -m experiments.coding4a --condition plain --model sonnet \
        --limit 3 --out results/coding4a-pilot-plain.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_QUIXBUGS = Path(
    "/private/tmp/claude-501/-Users-hirotaka-neuro-symbolic-ai/"
    "bef9b3dc-a1f8-4890-b6fc-55d770d86203/scratchpad/quixbugs-cache"
)

CONDITIONS = ("plain", "nsai")

# With permission_mode="dontAsk", allowed_tools alone does NOT restrict
# anything — the built-in set must be limited via tools=[...] or the agent
# keeps Bash/network access (same pitfall documented in nsai.agent).
FILE_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"]

PLAIN_SYSTEM = (
    "You are a careful senior software engineer. The workspace contains a single "
    "Python file with exactly one small defect (often a boundary, off-by-one, or "
    "operator error). Read the code carefully, reason about edge cases and "
    "boundary conditions, and fix the defect in place with a minimal edit. "
    "Do not rewrite the algorithm, rename anything, or change the function "
    "signature."
)

NSAI_SYSTEM = PLAIN_SYSTEM + (
    " You also have symbolic reasoning tools (SMT solver, CSP solver, knowledge "
    "base). Before declaring the fix done, express the function's boundary "
    "conditions as SMT constraints and check them with the smt tools: encode the "
    "suspected boundary/invariant (loop bounds, index ranges, termination, "
    "comparison direction) with smt_verify, confirm your fixed logic satisfies "
    "it, and if the solver returns a counterexample, re-examine the code before "
    "finishing."
)

TASK_PROMPT = (
    "The file {name}.py contains exactly one small bug (often a "
    "boundary/off-by-one/operator error). Find it, fix it in place with a "
    "minimal edit, and briefly state the defect."
)

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def list_programs(quixbugs: Path) -> list[str]:
    """Program names that have both a buggy file and json testcases.

    Graph-based QuixBugs programs ship no json testcases (they need Node
    objects); anything without a json testcase file is skipped.
    """
    names = []
    for tc in sorted((quixbugs / "json_testcases").glob("*.json")):
        if (quixbugs / "python_programs" / f"{tc.stem}.py").exists():
            names.append(tc.stem)
    return names


def _arity_bounds(correct_file: Path, func_name: str) -> tuple[int, int]:
    """(required, total) positional-parameter counts of the reference function."""
    spec = importlib.util.spec_from_file_location(f"_ref_{func_name}", correct_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    params = [
        p
        for p in inspect.signature(getattr(mod, func_name)).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    required = sum(1 for p in params if p.default is p.empty)
    return required, len(params)


def load_cases(quixbugs: Path, name: str) -> list[tuple[list, object]]:
    """Parse json_testcases/<name>.json into (args, expected) pairs.

    Each line is [input, expected]. `input` is usually the argument list, but
    for single-argument functions it can be the bare argument (or a list that
    IS the single argument, e.g. flatten). Disambiguate with the reference
    implementation's signature arity — the reference is only inspected here,
    never shown to the agent.
    """
    lo, hi = _arity_bounds(quixbugs / "correct_python_programs" / f"{name}.py", name)
    cases: list[tuple[list, object]] = []
    text = (quixbugs / "json_testcases" / f"{name}.json").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        inp, expected = json.loads(line)
        if not isinstance(inp, list):
            args = [inp]
        elif lo <= len(inp) <= hi:
            args = inp
        else:
            args = [inp]  # the list itself is the single argument
        cases.append((args, expected))
    return cases


# ---------------------------------------------------------------------------
# Grading (stdlib only — runs the candidate in a subprocess per case)
# ---------------------------------------------------------------------------

# Executed as `python -c GRADER file func args_json expected_json abs_tol`.
# Exhausts generators, compares floats approximately, and treats tuples as
# lists (JSON testcases can't express tuples). Prints PASS or FAIL:<got>.
_GRADER = """\
import importlib.util, json, math, sys, types
path, func_name, args_json, expected_json, tol = sys.argv[1:6]
spec = importlib.util.spec_from_file_location("prog_under_test", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
out = getattr(mod, func_name)(*json.loads(args_json))
if isinstance(out, types.GeneratorType):
    out = list(out)
def close(a, b):
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=float(tol))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b
print("PASS" if close(out, json.loads(expected_json)) else "FAIL:" + repr(out)[:200])
"""


def _case_tol(func_name: str, args: list) -> float:
    """Absolute comparison tolerance for one case.

    QuixBugs' own pytest tester grades sqrt(x, epsilon) with
    `pytest.approx(expected, abs=epsilon)` — the stored expected values differ
    from the reference output by more than 1e-6. Everything else is exact.
    """
    if func_name == "sqrt" and len(args) == 2:
        return float(args[1])
    return 1e-9


def grade(
    program_file: Path, func_name: str, cases: list[tuple[list, object]],
    timeout: float = 10.0,
) -> dict:
    """Run every (args, expected) case against program_file in a subprocess.

    Returns {"passed": bool, "n_cases": int, "cases_failed": [indices]}.
    A crash or per-case timeout counts as a failure for that case.
    """
    failed: list[int] = []
    for i, (args, expected) in enumerate(cases):
        cmd = [
            sys.executable, "-c", _GRADER,
            str(program_file), func_name, json.dumps(args), json.dumps(expected),
            str(_case_tol(func_name, args)),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            ok = proc.returncode == 0 and proc.stdout.strip() == "PASS"
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            failed.append(i)
    return {"passed": not failed, "n_cases": len(cases), "cases_failed": failed}


def prepare_cases(
    quixbugs: Path, name: str, timeout: float = 10.0
) -> tuple[list[tuple[list, object]], list[int], list[int]]:
    """Load cases and drop any the REFERENCE implementation itself fails
    within the timeout (e.g. knapsack case 9 / levenshtein case 3 have inputs
    the naive reference algorithms cannot finish in 10s — grading a candidate
    on those would mark correct fixes as failures).

    Returns (kept_cases, kept_original_indices, skipped_original_indices).
    """
    cases = load_cases(quixbugs, name)
    ref = grade(
        quixbugs / "correct_python_programs" / f"{name}.py", name, cases, timeout
    )
    skipped = ref["cases_failed"]
    kept_ids = [i for i in range(len(cases)) if i not in skipped]
    return [cases[i] for i in kept_ids], kept_ids, skipped


# ---------------------------------------------------------------------------
# Agent runner (SDK imported lazily — grading and tests stay offline)
# ---------------------------------------------------------------------------


def build_task_options(condition: str, model: str | None, workdir: Path, kb_path: Path):
    """ClaudeAgentOptions for one task. Built here on purpose — the experiment
    conditions must not drift with nsai.agent.build_options."""
    from claude_agent_sdk import ClaudeAgentOptions

    common = dict(
        model=model,
        cwd=str(workdir),
        permission_mode="dontAsk",
        setting_sources=[],
        tools=list(FILE_TOOLS),  # limits the built-in set (see FILE_TOOLS note)
    )
    if condition == "plain":
        return ClaudeAgentOptions(
            system_prompt=PLAIN_SYSTEM,
            allowed_tools=list(FILE_TOOLS),
            **common,
        )
    from nsai.kb import KnowledgeBase
    from nsai.tools import ALLOWED_TOOL_NAMES, build_server

    kb = KnowledgeBase(kb_path)  # throwaway scratch KB, one per task
    return ClaudeAgentOptions(
        system_prompt=NSAI_SYSTEM,
        mcp_servers={"symbolic": build_server(kb)},
        allowed_tools=list(FILE_TOOLS) + list(ALLOWED_TOOL_NAMES),
        **common,
    )


async def run_task(
    name: str, condition: str, model: str | None, quixbugs: Path,
    cases: list[tuple[list, object]],
) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        ResultMessage,
        ToolUseBlock,
    )

    tool_calls: dict[str, int] = {}
    cost = None
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        # The workdir holds ONLY the buggy file; the scratch KB lives beside
        # it, outside the agent's cwd. Tests/correct versions never enter.
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        target = workdir / f"{name}.py"
        shutil.copy(quixbugs / "python_programs" / f"{name}.py", target)
        options = build_task_options(condition, model, workdir, Path(tmp) / "kb.ttl")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(TASK_PROMPT.format(name=name))
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            tool_calls[block.name] = tool_calls.get(block.name, 0) + 1
                elif isinstance(message, ResultMessage):
                    cost = message.total_cost_usd
        result = grade(target, name, cases)
    return {
        "task_id": name,
        "condition": condition,
        "model": model,
        "passed": result["passed"],
        "n_cases": result["n_cases"],
        "cases_failed": result["cases_failed"],
        "cost_usd": cost,
        "seconds": round(time.monotonic() - start, 2),
        "tool_calls": tool_calls,
    }


async def run_all(
    programs: list[str], condition: str, model: str | None,
    runs: int, quixbugs: Path, out: Path,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Reference-feasibility filter once per program (10s burned per skipped
    # case, so don't repeat it across runs).
    prepared = {name: prepare_cases(quixbugs, name) for name in programs}
    for run in range(runs):
        for i, name in enumerate(programs):
            cases, kept_ids, skipped = prepared[name]
            record = await run_task(name, condition, model, quixbugs, cases)
            # Map failure positions back to original testcase-line indices.
            record["cases_failed"] = [kept_ids[p] for p in record["cases_failed"]]
            record["cases_skipped"] = skipped
            record["run"] = run
            # Open per record: a long-lived handle loses everything after its
            # inode is replaced, and one lost record is recoverable.
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            mark = "+" if record["passed"] else "-"
            print(
                f"[{condition} run{run} {i + 1}/{len(programs)}] {mark} {name} "
                f"failed_cases={record['cases_failed']} "
                f"cost=${(record['cost_usd'] or 0):.4f}",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="Experiment 4a: QuixBugs plain vs nsai")
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--model", default=None, help="Model alias (e.g. sonnet) or full id")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N programs")
    ap.add_argument("--programs", default=None,
                    help="Comma-separated explicit program list (default: all with testcases)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True, help="Results JSONL (appended)")
    ap.add_argument("--quixbugs", type=Path, default=DEFAULT_QUIXBUGS,
                    help="Path to a QuixBugs checkout")
    args = ap.parse_args()

    if args.programs:
        programs = [p.strip() for p in args.programs.split(",") if p.strip()]
        missing = [p for p in programs if p not in set(list_programs(args.quixbugs))]
        if missing:
            ap.error(f"no json testcases for: {', '.join(missing)}")
    else:
        programs = list_programs(args.quixbugs)
    if args.limit:
        programs = programs[: args.limit]

    asyncio.run(
        run_all(programs, args.condition, args.model, args.runs, args.quixbugs, args.out)
    )


if __name__ == "__main__":
    main()
