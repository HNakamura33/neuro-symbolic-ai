"""In-process MCP tools exposing the symbolic layer to the Claude agent."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .csp import solve_csp
from .kb import KnowledgeBase, QueryTimeout, TermParseError
from .smt import smt_verify as _smt_verify

SERVER_NAME = "symbolic"

# Set by build_server(); tools close over this module-level reference.
_kb: KnowledgeBase | None = None


def _text(payload: Any, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def _kb_or_err() -> KnowledgeBase:
    assert _kb is not None, "build_server() must be called first"
    return _kb


_TRIPLES_SCHEMA = {
    "type": "object",
    "properties": {
        "triples": {
            "type": "array",
            "description": "List of [subject, predicate, object] string triples.",
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "source": {
            "type": "string",
            "description": "Where these facts came from (document name, 'user statement', URL...). "
            "Recorded in the provenance log.",
        },
    },
    "required": ["triples"],
}


@tool(
    "kb_add_triples",
    "Assert facts into the knowledge graph. Each triple is [subject, predicate, object]. "
    "Use CURIEs (ns:alice, rdf:type, rdfs:subClassOf, owl:FunctionalProperty), "
    "quoted strings or bare numbers for literal objects.",
    _TRIPLES_SCHEMA,
)
async def kb_add_triples(args: dict[str, Any]) -> dict[str, Any]:
    try:
        added = _kb_or_err().add_triples(
            [tuple(t) for t in args["triples"]], source=args.get("source")
        )
        return _text({"added": added, "total_triples": _kb_or_err().stats()["triples"]})
    except TermParseError as e:
        return _text(f"Term parse error: {e}", is_error=True)


@tool(
    "kb_find",
    "Pattern-match triples in the knowledge graph. Omit a field to use it as a wildcard. "
    "After kb_infer has run, each match carries a fourth element tagging it "
    "'asserted' or 'inferred'.",
    {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "object": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
    },
)
async def kb_find(args: dict[str, Any]) -> dict[str, Any]:
    kb = _kb_or_err()
    try:
        rows = kb.find(
            subject=args.get("subject"),
            predicate=args.get("predicate"),
            obj=args.get("object"),
            limit=args.get("limit", 50),
            # Tagging is only informative once inferred triples are mixed in.
            with_origin=kb.inferred_triple_count > 0,
        )
        return _text({"matches": [list(r) for r in rows], "count": len(rows)})
    except TermParseError as e:
        return _text(f"Term parse error: {e}", is_error=True)


@tool(
    "kb_sparql",
    "Run a SPARQL SELECT or ASK query against the knowledge graph. "
    "Prefixes ns:, rdf:, rdfs:, owl:, xsd: are pre-bound "
    "(ns: = <http://nsai.local/ns#>).",
    {"query": str},
)
async def kb_sparql(args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = _kb_or_err().sparql(args["query"])
        return _text({"result": result})
    except Exception as e:  # rdflib raises many parser error types
        return _text(f"SPARQL error: {e}", is_error=True)


@tool(
    "kb_verify",
    "Verify a single atomic claim [subject, predicate, object] against the knowledge "
    "graph WITH OWL-RL inference. Returns verdict: entailed | contradicted | unknown.",
    {"subject": str, "predicate": str, "object": str},
)
async def kb_verify(args: dict[str, Any]) -> dict[str, Any]:
    try:
        r = _kb_or_err().verify_triple(args["subject"], args["predicate"], args["object"])
        return _text({"verdict": r.verdict, "detail": r.detail})
    except TermParseError as e:
        return _text(f"Term parse error: {e}", is_error=True)


@tool(
    "kb_check_answer",
    "Deterministic sanity gate for a candidate FINAL answer to a knowledge-graph "
    "question. Runs three checks against the KB and returns verdict pass | warn | "
    "reject with per-check reasons: (1) existence — the answer actually occurs in "
    "the KB (fabricated or wrongly-cased IDs are rejected, with the correctly-cased "
    "candidates suggested); (2) type — the answer occurs with the given final-hop "
    "relation, i.e. it is the right kind of entity for the question; (3) "
    "start-exclusion — warns when the answer is the question's start entity or a "
    "direct one-hop neighbor of it, the dominant wrong-answer mode in multi-hop "
    "questions. Treat reject as 'discard and keep exploring' and warn as "
    "'re-derive the full hop chain before trusting this answer'.",
    {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Candidate answer as a CURIE or literal (e.g. ns:Some_Entity).",
            },
            "start": {
                "type": "string",
                "description": "The question's start entity (CURIE).",
            },
            "relation": {
                "type": "string",
                "description": "The final-hop predicate expected to link to the answer "
                "(e.g. ns:release_year for a 'when was ... released' question).",
            },
        },
        "required": ["answer"],
    },
)
async def kb_check_answer(args: dict[str, Any]) -> dict[str, Any]:
    try:
        r = _kb_or_err().check_answer(
            args["answer"], start=args.get("start"), relation=args.get("relation")
        )
        return _text({"verdict": r.verdict, "checks": [asdict(c) for c in r.checks]})
    except TermParseError as e:
        return _text(f"Term parse error: {e}", is_error=True)


@tool(
    "kb_infer",
    "Materialize all RDFS/OWL-RL inferred triples into the knowledge graph. "
    "Unsound on a KB that contains contradictions — use kb_violations to audit those.",
    {"type": "object", "properties": {}},
)
async def kb_infer(args: dict[str, Any]) -> dict[str, Any]:
    added = _kb_or_err().infer()
    return _text(
        {
            "inferred_triples_added": added,
            "total_triples": _kb_or_err().stats()["triples"],
            "warning": (
                "If the KB contains contradictions, this closure is unsound: a "
                "functional-property conflict entails owl:sameAs between the "
                "conflicting objects, and the resulting sameAs chains merge "
                "unrelated entities, mass-producing spurious 'violations' and "
                "facts. Do not report closure-derived conflicts as findings — "
                "use kb_violations, which enumerates functional-property "
                "violations over the asserted triples only. Subsequent kb_find "
                "results tag each triple asserted vs inferred."
            ),
        }
    )


@tool(
    "kb_stats",
    "Get knowledge graph size statistics.",
    {"type": "object", "properties": {}},
)
async def kb_stats(args: dict[str, Any]) -> dict[str, Any]:
    return _text(_kb_or_err().stats())


@tool(
    "csp_solve",
    "Solve a constraint-satisfaction problem exactly. Provide variables (name -> list "
    "of possible values), constraints (python boolean expressions over variable names, "
    "e.g. 'a != b', 'start_a + 2 <= start_b'), and optionally all_different.",
    {
        "type": "object",
        "properties": {
            "variables": {
                "type": "object",
                "additionalProperties": {"type": "array"},
                "description": "Mapping of variable name to its domain (list of values).",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Boolean expressions over the variable names.",
            },
            "all_different": {"type": "boolean", "default": False},
        },
        "required": ["variables"],
    },
)
async def csp_solve(args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = solve_csp(
            variables=args["variables"],
            constraints=args.get("constraints", []),
            all_different=args.get("all_different", False),
        )
    except SyntaxError as e:
        return _text(f"Constraint syntax error: {e}", is_error=True)
    if "error" in result:
        return _text(result["error"], is_error=True)
    return _text(result)


@tool(
    "kb_provenance",
    "Look up where a stored triple [subject, predicate, object] came from "
    "(source and timestamp records).",
    {"subject": str, "predicate": str, "object": str},
)
async def kb_provenance(args: dict[str, Any]) -> dict[str, Any]:
    try:
        hits = _kb_or_err().provenance(args["subject"], args["predicate"], args["object"])
        return _text({"records": hits, "count": len(hits)})
    except TermParseError as e:
        return _text(f"Term parse error: {e}", is_error=True)


@tool(
    "kb_violations",
    "Enumerate ALL functional-property violations in one call, computed over the "
    "asserted triples only (pre-inference), each with the provenance records of "
    "every conflicting value. Prefer this over closure-based sweeps for "
    "contradiction audits: OWL-RL inference on a contradictory KB merges entities "
    "through owl:sameAs chains and manufactures spurious conflicts.",
    {"type": "object", "properties": {}},
)
async def kb_violations(args: dict[str, Any]) -> dict[str, Any]:
    try:
        violations = _kb_or_err().functional_violations()
        return _text({"violations": violations, "count": len(violations)})
    except QueryTimeout as e:
        return _text(str(e), is_error=True)


@tool(
    "smt_verify",
    "Prove or refute a numeric/boolean claim with the Z3 theorem prover. "
    "Declare typed variables (int | real | bool), give assumptions as python-style "
    "expressions (use And/Or/Not/Implies for connectives, e.g. 'x + y <= 10', "
    "'Implies(a, b > 0)'), and a goal. Returns proved | refuted (+counterexample) "
    "| consistent/inconsistent when no goal is given.",
    {
        "type": "object",
        "properties": {
            "variables": {
                "type": "object",
                "additionalProperties": {"type": "string", "enum": ["int", "real", "bool"]},
                "description": "Variable name -> type.",
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "goal": {"type": "string", "description": "Claim to prove. Omit to just check consistency."},
        },
        "required": ["variables", "assumptions"],
    },
)
async def smt_verify(args: dict[str, Any]) -> dict[str, Any]:
    result = _smt_verify(
        variables=args["variables"],
        assumptions=args.get("assumptions", []),
        goal=args.get("goal"),
    )
    if "error" in result:
        return _text(result["error"], is_error=True)
    return _text(result)


ALL_TOOLS = [
    kb_add_triples,
    kb_find,
    kb_sparql,
    kb_verify,
    kb_check_answer,
    kb_infer,
    kb_stats,
    kb_provenance,
    kb_violations,
    csp_solve,
    smt_verify,
]

ALLOWED_TOOL_NAMES = [f"mcp__{SERVER_NAME}__{t.name}" for t in ALL_TOOLS]


def build_server(kb: KnowledgeBase):
    """Bind the tools to a KnowledgeBase instance and return the MCP server config."""
    global _kb
    _kb = kb
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=ALL_TOOLS)
