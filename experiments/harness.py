"""Experiment runner — executes tasks under conditions B0/B1/B1p/C1/C2.

Conditions (docs/experiment-plan.md):

- B0   LLM only, no tools, no facts        (contamination probe)
- B1   LLM only, full KB inlined as Turtle (long-context baseline)
- B1p  LLM only, oracle-BFS retrieval: the question entity's k-hop
       neighborhood (k = the question's hop count unless --b1p-radius fixes
       it) inlined as Turtle, distance-ordered and truncated at
       --b1p-max-triples. The strongest realistic RAG stand-in for KBs too
       large to inline whole (MetaQA); qa task type only. Each record
       carries b1p_gold_in_context so EM failures decompose into
       "context could not contain the facts" vs "model missed them".
- B2   agentic-grep: the same agent loop with Grep/Read over the raw
       kb.ttl in its working directory — no symbolic tools. Separates the
       contribution of AGENCY (iterative tool use, which C1 also has) from
       the contribution of the SYMBOLIC layer: B1p→B2 isolates agency,
       B2→C1 isolates symbolic grounding.
- C1   nsai symbolic tools, no subagents   (symbolic-layer ablation)
- C2   nsai full (subagents + Task)
- C2f  C2 with delegation FORCED by the prompt (plan §リスク "委譲不履行"):
       measured C2 delegation was 0/600 on MetaQA, so the C2−C1 gap says
       nothing about delegation; C2f separates the delegation ceiling from
       the main agent's willingness to delegate. qa task type only.

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

CONDITIONS = ("B0", "B1", "B1p", "B2", "C1", "C2", "C2f")

BASELINE_SYSTEM = (
    "You are a careful reasoner. Follow the task instructions exactly. "
    "Think step by step, then end your reply with the required FINAL line."
)

# B2: a Claude-Code-style searcher — same agent loop as C1/C2 but its only
# access to the KB is text search over the raw Turtle file.
AGENTIC_GREP_SYSTEM = (
    "You are a careful reasoner with file-search tools. The knowledge base "
    "is a Turtle file (kb.ttl) in your working directory; one triple per "
    "statement, entities and predicates use the ns: prefix. Answer questions "
    "by searching it with Grep and reading matching regions with Read — "
    "chain searches for multi-hop questions, and check both edge directions "
    "(the entity may appear as subject or object). Keep patterns specific "
    "and use Grep's head_limit: hub entities match thousands of lines and "
    "oversized dumps are truncated. Follow the task instructions exactly "
    "and end with the required FINAL line."
)

#: Tool results stream back as single JSON messages; the SDK default buffer
#: (1 MiB) dies on a broad Grep over the 4.3 MB MetaQA Turtle file (observed
#: live: hub-entity match killed a B2 run at task 100).
MAX_BUFFER_SIZE = 10_000_000

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

# C2f: the delegation mandate appended to QA_TASK. Mirrors the audit
# prompt's synchronous-delegation contract (prompt_rev 2). prompt_rev 3
# adds the structured-handoff copy rule: the subagent's FINAL line is
# authoritative and must be copied verbatim.
QA_DELEGATE = """\

You MUST delegate the graph exploration to the symbolic-explorer subagent \
(Task tool). Run it synchronously (run_in_background: false), wait for the \
verified path it reports, then state the answer and the FINAL line yourself. \
Answering without having invoked symbolic-explorer is a failure.

The subagent's report ends with a line of the form `FINAL: ns:<answer>` (or \
`FINAL: unknown`). Copy that FINAL line's value VERBATIM as your own FINAL \
line — no paraphrasing, no reinterpretation, no substituting a different \
candidate you consider better. Only if the subagent's report contains no \
FINAL line may you extract the answer from the body of its report.
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
PROMPT_REV = 3


def render_prompt(task: dict, task_type: str, condition: str, facts_ttl: str | None) -> str:
    if condition == "B0":
        source_hint = " (answer from the claim itself; you are given no facts)"
    elif condition == "B1":
        source_hint = " provided below"
    elif condition == "B1p":
        source_hint = (
            " provided below (facts retrieved around the question entity; "
            "answer only from these facts)"
        )
    elif condition == "B2":
        source_hint = (
            " stored as Turtle in kb.ttl in your working directory — search "
            "it with Grep and Read (entities look like ns:Some_Name)"
        )
    else:
        source_hint = " via your symbolic tools (kb_verify, kb_sparql, kb_find)"
    if task_type == "claims":
        body = CLAIM_TASK.format(source_hint=source_hint, **task)
    elif task_type == "qa":
        body = QA_TASK.format(source_hint=source_hint, question=task["question"])
        if condition == "C2f":
            body += QA_DELEGATE
    else:
        body = AUDIT_TASK.format(source_hint=source_hint)
    if condition in ("B1", "B1p") and facts_ttl:
        body += f"\n--- KNOWLEDGE BASE (Turtle) ---\n{facts_ttl}\n"
    return body


def normalize_answer(ans: str) -> str:
    """Strip trailing punctuation the model may append after the answer.

    Gold answers MUST pass through the same normalization before comparison:
    entities can legitimately end in "." (ns:Robert_Downey_Jr.), so stripping
    only the prediction side makes them structurally unmatchable.
    """
    return ans.rstrip(".,;:")


def parse_final(text: str) -> str | None:
    matches = _FINAL_RE.findall(text)
    return normalize_answer(matches[-1].strip()) if matches else None


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
    options = _condition_options(kb_path, condition, model)
    options.max_buffer_size = MAX_BUFFER_SIZE
    return options


def _condition_options(
    kb_path: Path, condition: str, model: str | None
) -> ClaudeAgentOptions:
    if condition in ("B0", "B1", "B1p"):
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
    if condition == "B2":
        return ClaudeAgentOptions(
            system_prompt=AGENTIC_GREP_SYSTEM,
            model=model,
            # Read-only text search over the scratch KB copy; cwd pins the
            # agent to the tempdir so only kb.ttl (+ prov sidecar) is visible.
            tools=["Grep", "Read", "Glob"],
            allowed_tools=["Grep", "Read", "Glob"],
            cwd=str(kb_path.parent),
            permission_mode="dontAsk",
            setting_sources=[],
        )
    kb = KnowledgeBase(kb_path)
    return build_options(kb, model, subagents=(condition in ("C2", "C2f")))


# ---------------------------------------------------------------------------
# B1p oracle-BFS retrieval
# ---------------------------------------------------------------------------


def load_neighborhood_index(kb_path: Path):
    """Parse the KB once into (triples, adjacency) for B1p extraction."""
    from collections import defaultdict

    from rdflib import Graph

    g = Graph()
    g.parse(kb_path, format="turtle")
    triples = sorted(g)  # deterministic order for reproducible truncation
    adj = defaultdict(list)
    for i, (s, _, o) in enumerate(triples):
        adj[s].append(i)
        adj[o].append(i)
    return triples, adj


def neighborhood(triples, adj, start, radius: int, max_triples: int):
    """Distance-ordered undirected BFS neighborhood of ``start``.

    Returns (selected triple list, truncated flag). Truncation keeps the
    closest triples first, so a dropped gold path always lies at the cap
    boundary or beyond — never an artifact of arbitrary ordering.
    """
    seen_entities = {start}
    seen_triples: set[int] = set()
    selected: list = []
    frontier = [start]
    truncated = False
    for _ in range(radius):
        next_frontier: list = []
        for entity in frontier:
            for i in adj.get(entity, ()):
                if i in seen_triples:
                    continue
                if len(selected) >= max_triples:
                    return selected, True
                seen_triples.add(i)
                s, _, o = triples[i]
                selected.append(triples[i])
                for node in (s, o):
                    if node not in seen_entities:
                        seen_entities.add(node)
                        next_frontier.append(node)
        frontier = next_frontier
    return selected, truncated


def neighborhood_facts(
    triples, adj, task: dict, radius: int | None, max_triples: int,
) -> tuple[str, dict]:
    """Per-task Turtle context + b1p_* record fields for one qa task."""
    from rdflib import Graph, URIRef

    from nsai.kb import NS

    r = radius if radius is not None else int(task["hops"])
    start = URIRef(str(NS) + task["start"].split(":", 1)[1])
    gold = URIRef(str(NS) + task["answer"].split(":", 1)[1])
    selected, truncated = neighborhood(triples, adj, start, r, max_triples)
    sub = Graph()
    sub.bind("ns", NS)
    for t in selected:
        sub.add(t)
    covered = any(gold in (s, o) for s, _, o in selected)
    ttl = sub.serialize(format="turtle")
    extras = {
        "b1p_radius": r,
        "b1p_triples": len(selected),
        "b1p_truncated": truncated,
        "b1p_gold_in_context": covered,
    }
    return ttl, extras


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
    kb_path: Path, facts_ttl: str | None, extras: dict | None = None,
) -> dict:
    options = build_condition_options(kb_path, condition, model)
    prompt = render_prompt(task, task_type, condition, facts_ttl)
    text_parts: list[str] = []
    tool_calls: dict[str, int] = {}
    cost = None
    error = None
    start = time.monotonic()
    try:
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
    except Exception as e:  # one broken session must not kill a 600-task run
        error = f"{type(e).__name__}: {e}"
    text = "\n".join(text_parts)
    pred = parse_final_pairs(text) if task_type == "audit" else parse_final(text)
    gold = gold_of(task, task_type)
    if task_type == "audit":
        correct = pred == gold  # replaced by the set comparison below
    else:
        correct = pred is not None and pred == normalize_answer(gold)
    record = {
        "task_id": task["id"],
        "task_type": task_type,
        "condition": condition,
        "model": model,
        "gold": gold,
        "pred": pred,
        "correct": correct,
        "cost_usd": cost,
        "seconds": round(time.monotonic() - start, 2),
        "tool_calls": tool_calls,
        "prompt_rev": PROMPT_REV,
    }
    if error:
        record["error"] = error
    if extras:
        record.update(extras)
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
    b1p_radius: int | None = None, b1p_max_triples: int = 4000,
) -> None:
    tasks = load_tasks(dataset, task_type)
    if limit:
        tasks = tasks[:limit]
    # The audit task runs against the contradiction-injected KB copy;
    # claims/qa gold labels are only valid against the clean kb.ttl.
    kb_name = "kb-audit.ttl" if task_type == "audit" else "kb.ttl"
    facts_ttl = None
    nbhd_index = None
    if condition == "B1":
        facts_ttl = (dataset / kb_name).read_text(encoding="utf-8")
        if task_type == "audit":
            prov = dataset / "kb-audit.prov.jsonl"
            if prov.exists():
                facts_ttl += "\n--- PROVENANCE LOG (JSONL) ---\n"
                facts_ttl += prov.read_text(encoding="utf-8")
    elif condition == "B1p":
        if task_type != "qa":
            raise SystemExit("B1p requires --task-type qa (needs a start entity)")
        nbhd_index = load_neighborhood_index(dataset / kb_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        with tempfile.TemporaryDirectory() as tmp:
            kb_copy = Path(tmp) / kb_name
            prov_src = dataset / f"{kb_name.removesuffix('.ttl')}.prov.jsonl"
            for i, task in enumerate(tasks):
                # Fresh scratch copy PER TASK: C1/C2 expose kb_add_triples,
                # and add_triples persists to the scratch file — a per-run
                # copy would leak task N's assertions into task N+1 and
                # corrupt gold "unknown" judgments.
                shutil.copy(dataset / kb_name, kb_copy)
                if prov_src.exists():  # provenance tool reads the sidecar
                    shutil.copy(prov_src, Path(tmp) / prov_src.name)
                task_facts, extras = facts_ttl, None
                if nbhd_index is not None:
                    task_facts, extras = neighborhood_facts(
                        *nbhd_index, task, b1p_radius, b1p_max_triples
                    )
                record = await run_task(
                    task, task_type, condition, model, kb_copy, task_facts, extras
                )
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
    ap.add_argument("--b1p-radius", type=int, default=None,
                    help="B1p: fixed BFS radius (default: each question's hop count)")
    ap.add_argument("--b1p-max-triples", type=int, default=4000,
                    help="B1p: context budget; farther triples are dropped first")
    args = ap.parse_args()
    if args.task_type == "audit" and args.condition == "B0":
        ap.error("audit requires a KB; B0 has none (use B1/C1/C2)")
    if args.condition == "B1p" and args.task_type != "qa":
        ap.error("B1p requires --task-type qa (needs a start entity)")
    if args.condition == "C2f" and args.task_type != "qa":
        ap.error("C2f (forced delegation) is defined for --task-type qa only")
    asyncio.run(
        run_dataset(args.dataset, args.task_type, args.condition, args.model,
                    args.runs, args.limit, args.out,
                    b1p_radius=args.b1p_radius, b1p_max_triples=args.b1p_max_triples)
    )


if __name__ == "__main__":
    main()
