"""Experiment runner — executes tasks under conditions B0/B1/C1/C2.

Conditions (docs/experiment-plan.md):

- B0  LLM only, no tools, no facts        (contamination probe)
- B1  LLM only, full KB inlined as Turtle (long-context baseline)
- C1  nsai symbolic tools, no subagents   (symbolic-layer ablation)
- C2  nsai full (subagents + Task)

Each task runs in a fresh session against a scratch copy of the dataset KB
(agents may assert triples; the pristine KB must survive across tasks).
Every run appends one JSON line to the results file with the prediction,
gold, cost, tool-call counts, and timing — grade.py / report.py consume it.

Usage:
    uv run python -m experiments.harness --dataset data/dev --task-type claims \
        --condition C1 --model haiku --runs 1 --out results/dev-c1.jsonl
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

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    ToolUseBlock,
)

from nsai.agent import build_options
from nsai.kb import KnowledgeBase

CONDITIONS = ("B0", "B1", "C1", "C2")

BASELINE_SYSTEM = (
    "You are a careful reasoner. Follow the task instructions exactly. "
    "Think step by step, then end your reply with the required FINAL line."
)

_FINAL_RE = re.compile(r"FINAL:\s*([^\s`*]+)")

CLAIM_TASK = """\
Decide whether the following claim is entailed by, contradicted by, or unknown to \
the knowledge base{source_hint}.
- entailed: the claim follows from the knowledge base (including RDFS/OWL inference \
such as subclass chains, functional properties, and sameAs).
- contradicted: the knowledge base entails something incompatible with the claim.
- unknown: the knowledge base neither entails nor contradicts it. Do not guess from \
world knowledge; if the knowledge base is silent, answer unknown.

Claim triple: ({subject}, {predicate}, {object})

End your reply with exactly one line: FINAL: entailed | FINAL: contradicted | FINAL: unknown
"""

QA_TASK = """\
Answer the question using the knowledge base{source_hint}.

Question: {question}

Reply with the entity CURIE only, ending with the line: FINAL: ns:<name>
"""


def render_prompt(task: dict, task_type: str, condition: str, facts_ttl: str | None) -> str:
    if condition == "B0":
        source_hint = " (answer from the claim itself; you are given no facts)"
    elif condition == "B1":
        source_hint = " provided below"
    else:
        source_hint = " via your symbolic tools (kb_verify, kb_sparql, kb_find)"
    if task_type == "claims":
        body = CLAIM_TASK.format(source_hint=source_hint, **task)
    else:
        body = QA_TASK.format(source_hint=source_hint, question=task["question"])
    if condition == "B1" and facts_ttl:
        body += f"\n--- KNOWLEDGE BASE (Turtle) ---\n{facts_ttl}\n"
    return body


def parse_final(text: str) -> str | None:
    matches = _FINAL_RE.findall(text)
    return matches[-1].strip().rstrip(".,;:") if matches else None


def build_condition_options(
    kb_path: Path, condition: str, model: str | None
) -> ClaudeAgentOptions:
    if condition in ("B0", "B1"):
        return ClaudeAgentOptions(
            system_prompt=BASELINE_SYSTEM,
            model=model,
            # tools=[] removes every built-in tool; allowed_tools=[] alone only
            # empties the auto-permission list, and with dontAsk the baseline
            # could still read the dataset KB off disk via Bash.
            tools=[],
            allowed_tools=[],
            permission_mode="dontAsk",
            setting_sources=[],
        )
    kb = KnowledgeBase(kb_path)
    return build_options(kb, model, subagents=(condition == "C2"))


def load_tasks(dataset: Path, task_type: str) -> list[dict]:
    path = dataset / ("claims.jsonl" if task_type == "claims" else "qa.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def gold_of(task: dict, task_type: str) -> str:
    return task["label"] if task_type == "claims" else task["answer"]


async def run_task(
    task: dict, task_type: str, condition: str, model: str | None,
    kb_path: Path, facts_ttl: str | None,
) -> dict:
    options = build_condition_options(kb_path, condition, model)
    prompt = render_prompt(task, task_type, condition, facts_ttl)
    text_parts: list[str] = []
    tool_calls: dict[str, int] = {}
    cost = None
    start = time.monotonic()
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
    pred = parse_final("\n".join(text_parts))
    gold = gold_of(task, task_type)
    record = {
        "task_id": task["id"],
        "task_type": task_type,
        "condition": condition,
        "model": model,
        "gold": gold,
        "pred": pred,
        "correct": pred == gold,
        "cost_usd": cost,
        "seconds": round(time.monotonic() - start, 2),
        "tool_calls": tool_calls,
    }
    if task_type == "claims":
        record["kind"] = task.get("kind")
    else:
        record["hops"] = task.get("hops")
    return record


async def run_dataset(
    dataset: Path, task_type: str, condition: str, model: str | None,
    runs: int, limit: int | None, out: Path,
) -> None:
    tasks = load_tasks(dataset, task_type)
    if limit:
        tasks = tasks[:limit]
    facts_ttl = (dataset / "kb.ttl").read_text(encoding="utf-8") if condition == "B1" else None
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for run in range(runs):
            # Scratch copy so agent-side kb_add_triples can't pollute the dataset.
            with tempfile.TemporaryDirectory() as tmp:
                kb_copy = Path(tmp) / "kb.ttl"
                shutil.copy(dataset / "kb.ttl", kb_copy)
                for i, task in enumerate(tasks):
                    record = await run_task(task, task_type, condition, model, kb_copy, facts_ttl)
                    record["run"] = run
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    mark = "+" if record["correct"] else "-"
                    print(f"[{condition} run{run} {i + 1}/{len(tasks)}] {mark} "
                          f"{task['id']} pred={record['pred']} gold={record['gold']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run experiment tasks under one condition")
    ap.add_argument("--dataset", type=Path, required=True, help="Directory from kbgen --out")
    ap.add_argument("--task-type", choices=("claims", "qa"), required=True)
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--model", default=None, help="Model alias (e.g. haiku) or full id")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="Only the first N tasks (pilot)")
    ap.add_argument("--out", type=Path, required=True, help="Results JSONL (appended)")
    args = ap.parse_args()
    asyncio.run(
        run_dataset(args.dataset, args.task_type, args.condition, args.model,
                    args.runs, args.limit, args.out)
    )


if __name__ == "__main__":
    main()
