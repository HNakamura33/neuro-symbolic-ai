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
_FINAL_LINE_RE = re.compile(r"FINAL:\s*(.+)")

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

AUDIT_TASK = """\
The knowledge base contains contradictions: functional properties asserted with \
conflicting values for the same subject{source_hint}.
Systematically find ALL of them — do not stop after the first few. Include \
conflicts that only surface through inference.

End your reply with exactly one line listing every conflicting (subject, predicate) \
pair, semicolon-separated:
FINAL: ns:subject|ns:predicate; ns:subject|ns:predicate

If you delegate any of the work, run the subagent synchronously (never in the \
background — pass run_in_background: false), wait for its results, then state the \
complete findings and the FINAL line yourself. A reply that only describes the \
delegation is a failure.
"""

# Bump when any task prompt changes; recorded per record for reproducibility.
PROMPT_REV = 2


def render_prompt(task: dict, task_type: str, condition: str, facts_ttl: str | None) -> str:
    if condition == "B0":
        source_hint = " (answer from the claim itself; you are given no facts)"
    elif condition == "B1":
        source_hint = " provided below"
    else:
        source_hint = " via your symbolic tools (kb_verify, kb_sparql, kb_find)"
    if task_type == "claims":
        body = CLAIM_TASK.format(source_hint=source_hint, **task)
    elif task_type == "qa":
        body = QA_TASK.format(source_hint=source_hint, question=task["question"])
    else:
        body = AUDIT_TASK.format(source_hint=source_hint)
    if condition == "B1" and facts_ttl:
        body += f"\n--- KNOWLEDGE BASE (Turtle) ---\n{facts_ttl}\n"
    return body


def parse_final(text: str) -> str | None:
    matches = _FINAL_RE.findall(text)
    return matches[-1].strip().rstrip(".,;:") if matches else None


def parse_final_pairs(text: str) -> list[str] | None:
    """Parse the audit FINAL line into a sorted, deduplicated list of s|p pairs."""
    matches = _FINAL_LINE_RE.findall(text)
    if not matches:
        return None
    pairs = (p.strip().strip("`*.").replace(" ", "") for p in matches[-1].split(";"))
    return sorted({p for p in pairs if p})


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
    if task_type == "audit":
        # One whole-KB sweep per run; gold is the injected conflict set.
        lines = (dataset / "injected.jsonl").read_text(encoding="utf-8").splitlines()
        injected = [json.loads(line) for line in lines if line]
        pairs = sorted({f"{r['subject']}|{r['predicate']}" for r in injected})
        return [{"id": "audit-0", "gold_pairs": pairs}]
    path = dataset / ("claims.jsonl" if task_type == "claims" else "qa.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def gold_of(task: dict, task_type: str) -> str | list[str]:
    if task_type == "audit":
        return task["gold_pairs"]
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
    text = "\n".join(text_parts)
    pred = parse_final_pairs(text) if task_type == "audit" else parse_final(text)
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
        "prompt_rev": PROMPT_REV,
    }
    if task_type == "claims":
        record["kind"] = task.get("kind")
    elif task_type == "qa":
        record["hops"] = task.get("hops")
    else:
        pred_set, gold_set = set(pred or []), set(gold)
        tp = len(pred_set & gold_set)
        record["correct"] = pred_set == gold_set
        record["precision"] = tp / len(pred_set) if pred_set else 0.0
        record["recall"] = tp / len(gold_set) if gold_set else 0.0
    return record


async def run_dataset(
    dataset: Path, task_type: str, condition: str, model: str | None,
    runs: int, limit: int | None, out: Path,
) -> None:
    tasks = load_tasks(dataset, task_type)
    if limit:
        tasks = tasks[:limit]
    # The audit task runs against the contradiction-injected KB copy;
    # claims/qa gold labels are only valid against the clean kb.ttl.
    kb_name = "kb-audit.ttl" if task_type == "audit" else "kb.ttl"
    facts_ttl = None
    if condition == "B1":
        facts_ttl = (dataset / kb_name).read_text(encoding="utf-8")
        if task_type == "audit":
            prov = dataset / "kb-audit.prov.jsonl"
            if prov.exists():
                facts_ttl += "\n--- PROVENANCE LOG (JSONL) ---\n"
                facts_ttl += prov.read_text(encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        # Scratch copy so agent-side kb_add_triples can't pollute the dataset.
        with tempfile.TemporaryDirectory() as tmp:
            kb_copy = Path(tmp) / kb_name
            shutil.copy(dataset / kb_name, kb_copy)
            prov_src = dataset / f"{kb_name.removesuffix('.ttl')}.prov.jsonl"
            if prov_src.exists():  # provenance tool reads the sidecar
                shutil.copy(prov_src, Path(tmp) / prov_src.name)
            for i, task in enumerate(tasks):
                record = await run_task(task, task_type, condition, model, kb_copy, facts_ttl)
                record["run"] = run
                # Open per record: a long-lived handle loses everything after
                # its inode is replaced, and one lost record is recoverable.
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                mark = "+" if record["correct"] else "-"
                print(f"[{condition} run{run} {i + 1}/{len(tasks)}] {mark} "
                      f"{task['id']} pred={record['pred']} gold={record['gold']}",
                      flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run experiment tasks under one condition")
    ap.add_argument("--dataset", type=Path, required=True, help="Directory from kbgen --out")
    ap.add_argument("--task-type", choices=("claims", "qa", "audit"), required=True)
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--model", default=None, help="Model alias (e.g. haiku) or full id")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="Only the first N tasks (pilot)")
    ap.add_argument("--out", type=Path, required=True, help="Results JSONL (appended)")
    args = ap.parse_args()
    if args.task_type == "audit" and args.condition == "B0":
        ap.error("audit requires a KB; B0 has none (use B1/C1/C2)")
    asyncio.run(
        run_dataset(args.dataset, args.task_type, args.condition, args.model,
                    args.runs, args.limit, args.out)
    )


if __name__ == "__main__":
    main()
