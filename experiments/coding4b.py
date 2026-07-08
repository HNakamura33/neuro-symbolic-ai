"""Experiment 4b runner — test-generation mutation power: LLM-written tests
vs the nsai test-generator subagent (docs/experiment-plan.md §実験4b).

Conditions:

- llm           the model writes pytest tests from the function's
                signature+docstring alone. Pure text: tools=[] (with
                permission_mode="dontAsk", allowed_tools alone restricts
                NOTHING — the built-in set must be emptied via tools=[]).
- nsai-testgen  minimal nsai coding config: symbolic MCP server + the
                test-generator subagent via Task. The workdir contains
                target.py (the correct implementation) because the
                test-generator by design Reads the code and encodes its
                branch/boundary conditions as SMT/CSP constraints — this
                information asymmetry vs `llm` is inherent to the design
                being measured, and is called out in the paper.

Grading is offline and LLM-free: every mutant from experiments.mutate is run
against the generated test file with pytest in a subprocess. Generated tests
are untrusted code — they execute in a scratch tempdir (never near the
dataset) with a per-mutant timeout. A mutant is killed when the suite fails
or times out on it; "no tests collected" kills nothing. mutation_score =
killed/total, boundary_score over comparison-swap + ±1-constant mutants only.
The generated test file itself is stored in each record so the "top-n tests"
equalized comparison can be recomputed later without new API calls.

Task sources:

- --source evalplus      HumanEval+/MBPP+ JSONL fetched straight from the
                         EvalPlus release assets (stdlib urllib, cached under
                         --cache; no evalplus/datasets dependency). Sample
                         boundary-heavy functions with --sample 40 --seed 7.
- --source <dir>         bugsuite-like directory: <dir>/<task>/correct.py +
                         meta.json {"entry_point": ..., optional "spec"}.

The SDK and nsai are imported lazily inside the runner so grading and tests
stay importable offline (same convention as coding4a).

Usage:
    uv run python -m experiments.coding4b --condition llm --model sonnet \
        --sample 40 --seed 7 --out results/coding4b-llm.jsonl
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import gzip
import json
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from experiments.mutate import BOUNDARY_CLASSES, Mutant, find_function, mutants, site_counts

CONDITIONS = ("llm", "nsai-testgen")

EVALPLUS_URLS = {
    "humaneval": "https://github.com/evalplus/humanevalplus_release/releases/"
                 "download/v0.1.10/HumanEvalPlus.jsonl.gz",
    "mbpp": "https://github.com/evalplus/mbppplus_release/releases/"
            "download/v0.2.0/MbppPlus.jsonl.gz",
}
DEFAULT_CACHE = Path("data/evalplus")

TARGET_FILENAME = "target.py"
TEST_FILENAME = "test_generated.py"

LLM_SYSTEM = (
    "You are an expert Python test engineer. You write small, sharp pytest "
    "suites that probe boundary conditions (off-by-one, comparison direction, "
    "empty/zero/negative inputs, exact threshold values) as well as typical "
    "cases. You are given only a function's signature and docstring — no "
    "implementation and no tools. Every assertion must use concrete values "
    "you are confident about from the documented contract."
)

LLM_TASK = """\
Write a pytest test file for the following function. The implementation is \
not shown; test the contract implied by the signature and docstring, with \
special attention to boundary values.

{spec}

Requirements:
- import the function with: from target import {entry_point}
- plain functions named test_*, each asserting concrete inputs and outputs
- reply with exactly one fenced ```python block containing the complete, \
self-contained test file
"""

NSAI_SYSTEM = (
    "You are a careful senior software engineer with symbolic reasoning "
    "tools. The workspace contains target.py with one function under test. "
    "Derive the tests via the test-generator subagent (Task tool): it Reads "
    "the code, encodes preconditions and branch conditions as SMT/CSP "
    "constraints, and produces counterexample, boundary-value, and "
    "equivalence-class test cases from the solvers. Always run the subagent "
    "synchronously (run_in_background: false) and wait for its results, then "
    "assemble one complete pytest file yourself — a reply that only "
    "describes the delegation is a failure."
)

NSAI_TASK = """\
The file target.py contains this function:

{spec}

Generate a pytest test file for it. Delegate the test derivation to the \
test-generator subagent (synchronously), then reply with exactly one fenced \
```python block containing the complete test file.

Requirements:
- import the function with: from target import {entry_point}
- plain functions named test_*, each asserting concrete inputs and outputs
- keep each solver-derived case as its own test, with a comment stating \
which constraint, boundary, or counterexample justifies it
"""

# ---------------------------------------------------------------------------
# Task sources
# ---------------------------------------------------------------------------


def _spec(source: str, entry_point: str, fallback_doc: str | None = None) -> str:
    """signature + docstring shown to the model (never the implementation)."""
    func = find_function(ast.parse(source), entry_point)
    sig = f"def {entry_point}({ast.unparse(func.args)})"
    if func.returns is not None:
        sig += f" -> {ast.unparse(func.returns)}"
    doc = (ast.get_docstring(func) or fallback_doc or "").replace('"""', "'''")
    return f'{sig}:\n    """{doc}"""\n'


def fetch_evalplus(dataset: str, cache_dir: Path) -> Path:
    """Download the EvalPlus release JSONL.gz once, then serve from cache."""
    url = EVALPLUS_URLS[dataset]
    dest = cache_dir / Path(url).name
    if not dest.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        urllib.request.urlretrieve(url, part)
        part.rename(dest)
    return dest


def load_evalplus(dataset: str, cache_dir: Path) -> list[dict]:
    """HumanEval+/MBPP+ tasks: {task_id, entry_point, correct_source, spec}.

    HumanEval rows: prompt is already signature+docstring and the canonical
    solution continues it. MBPP rows: the canonical solution is a complete
    module and the NL prompt becomes the docstring of a synthesized spec.
    """
    tasks = []
    with gzip.open(fetch_evalplus(dataset, cache_dir), "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            entry_point = row["entry_point"]
            if dataset == "humaneval":
                correct = row["prompt"] + row["canonical_solution"]
                spec = row["prompt"]
            else:
                correct = row["canonical_solution"]
                spec = _spec(correct, entry_point, fallback_doc=row["prompt"].strip())
            tasks.append({
                "task_id": row["task_id"].replace("/", "-"),
                "entry_point": entry_point,
                "correct_source": correct,
                "spec": spec,
            })
    return tasks


def load_dir(root: Path) -> list[dict]:
    """bugsuite-like layout: <root>/<task>/correct.py + meta.json.

    Infeasible variants (contradictory spec, hence no correct.py) belong to
    experiment 4c only — mutation grading needs a correct implementation to
    mutate, so they are skipped here.
    """
    tasks = []
    for meta_path in sorted(root.glob("*/meta.json")):
        correct_path = meta_path.parent / "correct.py"
        if not correct_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        source = correct_path.read_text(encoding="utf-8")
        entry_point = meta["entry_point"]
        tasks.append({
            "task_id": meta.get("task_id", meta_path.parent.name),
            "entry_point": entry_point,
            "correct_source": source,
            "spec": meta.get("spec") or _spec(source, entry_point),
        })
    return tasks


def sample_tasks(
    tasks: list[dict], n: int | None, seed: int, min_boundary: int = 3
) -> list[dict]:
    """Boundary-heavy sample: tasks with no mutation sites are always dropped;
    with n set, only tasks with >= min_boundary boundary mutants are eligible
    and n of them are drawn with the seeded RNG. Deterministic."""
    eligible = []
    for task in sorted(tasks, key=lambda t: t["task_id"]):
        try:
            counts = site_counts(task["correct_source"], task["entry_point"])
        except (SyntaxError, ValueError):
            continue
        if not counts:
            continue
        boundary = sum(v for k, v in counts.items() if k in BOUNDARY_CLASSES)
        if n is not None and boundary < min_boundary:
            continue
        eligible.append(task)
    if n is None or n >= len(eligible):
        return eligible
    picked = random.Random(seed).sample(eligible, n)
    return sorted(picked, key=lambda t: t["task_id"])


# ---------------------------------------------------------------------------
# Test-file extraction
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)\n?```", re.DOTALL)


def extract_test_source(text: str) -> str | None:
    """The last fenced code block that defines tests (last block otherwise)."""
    blocks = _FENCE_RE.findall(text)
    with_tests = [b for b in blocks if "def test" in b]
    pick = with_tests[-1] if with_tests else (blocks[-1] if blocks else None)
    return pick.strip() + "\n" if pick else None


def count_tests(test_source: str | None) -> int:
    """Top-level and nested test_* functions (n for the later top-n analysis)."""
    if not test_source:
        return 0
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


# ---------------------------------------------------------------------------
# Grading (stdlib + pytest subprocess only — no LLM, no SDK)
# ---------------------------------------------------------------------------


def run_pytest(target_source: str, test_source: str, timeout: float) -> str:
    """Run the generated tests against one implementation.

    Untrusted code: executes in a subprocess with cwd set to a scratch
    tempdir holding only target.py + the test file (never the dataset dir).
    Returns "pass" | "fail" | "timeout" | "none" (no tests collected).
    """
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, TARGET_FILENAME).write_text(target_source, encoding="utf-8")
        Path(tmp, TEST_FILENAME).write_text(test_source, encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "-x",
               "-p", "no:cacheprovider", TEST_FILENAME]
        try:
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "timeout"
        if proc.returncode == 0:
            return "pass"
        return "none" if proc.returncode == 5 else "fail"


def grade_tests(
    test_source: str | None, correct_source: str, muts: list[Mutant],
    timeout: float = 10.0,
) -> dict:
    """Kill matrix + scores for one generated test file.

    killed = the suite fails or times out on the mutant (the original must
    finish within the same timeout, so a timeout is a behavioral change).
    An empty/unextractable/collection-free test file kills nothing.
    """
    if test_source:
        baseline = run_pytest(correct_source, test_source, timeout)
    else:
        baseline = "none"
    if baseline == "pass":
        kill = [
            1 if run_pytest(m.source, test_source, timeout) in ("fail", "timeout")
            else 0
            for m in muts
        ]
    else:
        # A suite that cannot pass the ORIGINAL provides no evidence: its
        # failures on mutants are indistinguishable from its failures on
        # correct code, so it kills nothing (otherwise an always-failing
        # suite would score 100%).
        kill = [0] * len(muts)
    n_boundary = sum(m.op_class in BOUNDARY_CLASSES for m in muts)
    killed_boundary = sum(
        k for k, m in zip(kill, muts) if m.op_class in BOUNDARY_CLASSES
    )
    return {
        "tests_pass_original": baseline == "pass",
        "kill_matrix": kill,
        "mutation_score": (sum(kill) / len(muts)) if muts else None,
        "boundary_score": (killed_boundary / n_boundary) if n_boundary else None,
        "n_mutants": len(muts),
        "n_boundary_mutants": n_boundary,
    }


# ---------------------------------------------------------------------------
# Agent runner (SDK/nsai imported lazily — grading and tests stay offline)
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
    )
    if condition == "llm":
        return ClaudeAgentOptions(
            system_prompt=LLM_SYSTEM,
            # dontAsk auto-allows every tool the runtime offers, so the
            # built-in set itself must be emptied via tools=[] —
            # allowed_tools=[] alone would leave file/bash/network open.
            tools=[],
            allowed_tools=[],
            **common,
        )
    from nsai.kb import KnowledgeBase
    from nsai.subagents import build_agents
    from nsai.tools import ALLOWED_TOOL_NAMES, build_server

    kb = KnowledgeBase(kb_path)  # throwaway scratch KB, one per task
    # Only the test-generator: build_agents(coding=True) also defines
    # loop-judge (wants Bash) — out of scope for 4b.
    agents = {"test-generator": build_agents(coding=True)["test-generator"]}
    return ClaudeAgentOptions(
        system_prompt=NSAI_SYSTEM,
        mcp_servers={"symbolic": build_server(kb)},
        agents=agents,
        # Task = delegation; Read so main/subagent can open target.py.
        # Same tools= pitfall as above: this list is the whole built-in set.
        tools=["Task", "Read"],
        allowed_tools=["Task", "Read"] + list(ALLOWED_TOOL_NAMES),
        **common,
    )


async def run_task(
    task: dict, condition: str, model: str | None, muts: list[Mutant],
    timeout: float,
) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        ResultMessage,
        ToolUseBlock,
    )

    text_parts: list[str] = []
    tool_calls: dict[str, int] = {}
    cost = None
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        # The workdir holds ONLY target.py (nsai condition); the scratch KB
        # lives beside it, outside the agent's cwd. The dataset dir, the
        # mutants, and the grading tree are never visible to the agent.
        workdir = Path(tmp) / "work"
        workdir.mkdir()
        if condition == "nsai-testgen":
            (workdir / TARGET_FILENAME).write_text(
                task["correct_source"], encoding="utf-8"
            )
        options = build_task_options(condition, model, workdir, Path(tmp) / "kb.ttl")
        template = LLM_TASK if condition == "llm" else NSAI_TASK
        prompt = template.format(spec=task["spec"], entry_point=task["entry_point"])
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            tool_calls[block.name] = tool_calls.get(block.name, 0) + 1
                        elif hasattr(block, "text"):
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    cost = message.total_cost_usd
    agent_seconds = round(time.monotonic() - start, 2)

    test_source = extract_test_source("\n".join(text_parts))
    grade_start = time.monotonic()
    graded = grade_tests(test_source, task["correct_source"], muts, timeout=timeout)
    return {
        "task_id": task["task_id"],
        "condition": condition,
        "model": model,
        "n_tests": count_tests(test_source),
        "mutation_score": graded["mutation_score"],
        "boundary_score": graded["boundary_score"],
        "n_mutants": graded["n_mutants"],
        "n_boundary_mutants": graded["n_boundary_mutants"],
        "kill_matrix": graded["kill_matrix"],
        "tests_pass_original": graded["tests_pass_original"],
        "cost_usd": cost,
        "seconds": agent_seconds,  # agent wall time; grading reported apart
        "grade_seconds": round(time.monotonic() - grade_start, 2),
        "tool_calls": tool_calls,
        # kept verbatim so the top-n equalized comparison can be rerun offline
        "test_source": test_source,
    }


async def run_all(
    tasks: list[dict], condition: str, model: str | None, runs: int,
    timeout: float, out: Path,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Mutants are deterministic — generate once, reuse across runs; the
    # kill_matrix order in every record is mutants()' order.
    prepared = {
        t["task_id"]: mutants(t["correct_source"], t["entry_point"]) for t in tasks
    }
    for run in range(runs):
        for i, task in enumerate(tasks):
            record = await run_task(
                task, condition, model, prepared[task["task_id"]], timeout
            )
            record["run"] = run
            # Open per record: a long-lived handle loses everything after its
            # inode is replaced, and one lost record is recoverable.
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            score = record["mutation_score"]
            bscore = record["boundary_score"]
            print(
                f"[{condition} run{run} {i + 1}/{len(tasks)}] {task['task_id']} "
                f"tests={record['n_tests']} "
                f"score={score if score is None else round(score, 3)} "
                f"boundary={bscore if bscore is None else round(bscore, 3)} "
                f"ok_on_original={record['tests_pass_original']} "
                f"cost=${(record['cost_usd'] or 0):.4f}",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Experiment 4b: LLM tests vs nsai test-generator (mutation score)"
    )
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--model", default=None, help="Model alias (e.g. sonnet) or full id")
    ap.add_argument("--source", default="evalplus",
                    help="'evalplus' or a directory of <task>/correct.py + meta.json")
    ap.add_argument("--dataset", choices=("humaneval", "mbpp"), default="humaneval",
                    help="EvalPlus dataset (only with --source evalplus)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                    help="Cache dir for the EvalPlus release files")
    ap.add_argument("--sample", type=int, default=None,
                    help="Draw N boundary-heavy tasks (seeded)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-boundary", type=int, default=3,
                    help="Boundary-mutant eligibility threshold for --sample")
    ap.add_argument("--tasks", default=None, help="Comma-separated explicit task ids")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N tasks")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="Per-mutant pytest timeout (seconds)")
    ap.add_argument("--out", type=Path, required=True, help="Results JSONL (appended)")
    args = ap.parse_args()

    if args.source == "evalplus":
        tasks = load_evalplus(args.dataset, args.cache)
    else:
        tasks = load_dir(Path(args.source))
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        tasks = [t for t in tasks if t["task_id"] in wanted]
        missing = wanted - {t["task_id"] for t in tasks}
        if missing:
            ap.error(f"unknown task ids: {', '.join(sorted(missing))}")
    tasks = sample_tasks(tasks, args.sample, args.seed, args.min_boundary)
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        ap.error("no eligible tasks after filtering")

    asyncio.run(
        run_all(tasks, args.condition, args.model, args.runs, args.timeout, args.out)
    )


if __name__ == "__main__":
    main()
