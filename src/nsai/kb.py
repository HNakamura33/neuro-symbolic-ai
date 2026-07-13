"""Knowledge base layer — rdflib graph with Turtle persistence and OWL-RL inference.

This is the "symbolic" half of the system. The LLM never touches the graph
directly; it goes through the tools in `tools.py`, which call into here.
"""

from __future__ import annotations

import json
import re
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import owlrl
from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.term import Node

# Default namespace for facts the agent asserts.
NS = Namespace("http://nsai.local/ns#")

_PREFIXES = {
    "ns": NS,
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": OWL,
    "xsd": XSD,
}

_CURIE_RE = re.compile(r"^([A-Za-z_][\w-]*):([\w.-]+)$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


#: rdflib evaluates SPARQL in pure Python with no interrupt point, and the
#: MCP server runs in the agent's process — an under-constrained join can
#: spin for hours and wedge the whole session (observed live on MetaQA,
#: 133k triples: one query held a harness stream at 100% CPU for 3h).
#: Same failure class as the unbounded-Z3 hang fixed in nsai.smt.
SPARQL_TIMEOUT_S = 30
CLOSURE_TIMEOUT_S = 120


class QueryTimeout(RuntimeError):
    """A KB operation exceeded its deadline (message is agent-facing)."""


@contextmanager
def _deadline(seconds: int, message: str):
    """SIGALRM-based deadline; message is .format()ed with the limit.

    Signal handlers only work in the main thread — elsewhere (or on
    platforms without SIGALRM) the operation runs unbounded, which keeps
    library use from other threads working at the cost of the guard.
    """
    def _raise(signum, frame):
        raise QueryTimeout(message.format(seconds))

    try:
        previous = signal.signal(signal.SIGALRM, _raise)
    except ValueError:  # not in the main thread
        yield
        return
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class TermParseError(ValueError):
    """Raised when a string can't be interpreted as an RDF term."""


def parse_term(text: str, *, as_object: bool = False) -> Node:
    """Parse a user/LLM-supplied string into an rdflib term.

    Accepted forms:
      - Full IRI:        <http://example.org/x>
      - CURIE:           ns:alice, rdfs:subClassOf
      - Quoted literal:  "Alice", 'Tokyo'      (objects only)
      - Number/boolean:  42, 3.14, true        (objects only)
      - Bare word:       alice  → ns:alice     (convenience fallback)
    """
    text = text.strip()
    if not text:
        raise TermParseError("empty term")

    if text.startswith("<") and text.endswith(">"):
        return URIRef(text[1:-1])

    if as_object:
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            return Literal(text[1:-1])
        if _NUMBER_RE.match(text):
            return Literal(int(text)) if "." not in text else Literal(float(text))
        if text.lower() in ("true", "false"):
            return Literal(text.lower() == "true", datatype=XSD.boolean)

    m = _CURIE_RE.match(text)
    if m:
        prefix, local = m.groups()
        ns = _PREFIXES.get(prefix)
        if ns is None:
            raise TermParseError(f"unknown prefix '{prefix}:' (known: {', '.join(_PREFIXES)})")
        return ns[local]

    # Bare word convenience: treat as a name in the default namespace.
    if re.match(r"^[\w.-]+$", text):
        return NS[text]

    raise TermParseError(f"cannot parse term: {text!r}")


def format_term(term: Node) -> str:
    """Render an rdflib term back into compact CURIE/literal syntax."""
    if isinstance(term, URIRef):
        s = str(term)
        for prefix, ns in _PREFIXES.items():
            base = str(ns)
            if s.startswith(base):
                return f"{prefix}:{s[len(base):]}"
        return f"<{s}>"
    if isinstance(term, Literal):
        return str(term)
    return str(term)


@dataclass
class VerifyResult:
    verdict: str  # "entailed" | "contradicted" | "unknown"
    detail: str


@dataclass
class CheckOutcome:
    check: str  # "existence" | "type" | "start_exclusion"
    status: str  # "pass" | "warn" | "reject" | "skipped"
    detail: str


@dataclass
class CheckAnswerResult:
    verdict: str  # "pass" | "warn" | "reject" (worst status across checks)
    checks: list[CheckOutcome]


#: kb_path caps: hub hops (e.g. inverse has_genre — thousands of movies) must
#: return a bounded, deterministic payload instead of flooding the context.
#: Truncation is applied to the OUTPUT only; the traversal itself is complete.
MAX_PATH_HOPS = 5
MAX_PATH_TERMINALS = 100
MAX_PATH_EDGES_PER_HOP = 200


@dataclass
class PathEdge:
    """One traversed edge — a real KB triple plus the direction it was walked."""

    subject: str
    predicate: str
    object: str
    direction: str  # "forward" (subject -> object) | "inverse" (object -> subject)


@dataclass
class PathHop:
    relation: str  # as supplied by the caller, including any '^' marker
    edges: list[PathEdge]  # actual triples traversed (capped at MAX_PATH_EDGES_PER_HOP)
    edges_truncated: bool
    frontier_size: int  # distinct entities reached after this hop (never capped)


@dataclass
class PathResult:
    terminals: list[str]
    terminals_truncated: bool
    excluded: list[str]  # entities removed from the terminals by exclude_start
    hops: list[PathHop]
    notes: list[str]


class KnowledgeBase:
    """RDF graph with Turtle-file persistence and OWL-RL closure."""

    def __init__(self, path: Path):
        self.path = path
        # Sidecar provenance log: one JSON line per asserted triple with a source.
        self.prov_path = path.with_suffix(".prov.jsonl")
        # OWL-RL closure is recomputed only after the graph changes (~1s at
        # 5k triples, so repeated verify_triple calls need the cache).
        self._closure_cache: Graph | None = None
        # Triples materialized by infer() rather than asserted; lets query
        # results distinguish asserted from inferred after materialization.
        self._inferred_triples: set[tuple[Node, Node, Node]] = set()
        # Lowercased-IRI index for check_answer's miscasing detection;
        # invalidated together with the closure cache on every mutation.
        self._name_index_cache: dict[str, set[URIRef]] | None = None
        self.graph = Graph()
        for prefix, ns in _PREFIXES.items():
            self.graph.bind(prefix, ns)
        if path.exists():
            self.graph.parse(path, format="turtle")

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.graph.serialize(destination=self.path, format="turtle")

    # -- mutation ------------------------------------------------------------

    def add_triples(
        self, triples: list[tuple[str, str, str]], source: str | None = None
    ) -> int:
        """Add (subject, predicate, object) string triples. Returns count added.

        If `source` is given, each newly added triple is logged to the sidecar
        provenance file with the source and a timestamp.
        """
        added: list[tuple[Node, Node, Node]] = []
        for s, p, o in triples:
            triple = (
                parse_term(s),
                parse_term(p),
                parse_term(o, as_object=True),
            )
            if triple not in self.graph:
                self.graph.add(triple)
                added.append(triple)
        if added:
            self._closure_cache = None
            self._name_index_cache = None
            self.save()
            if source:
                self._log_provenance(added, source)
        return len(added)

    def _log_provenance(self, triples: list[tuple[Node, Node, Node]], source: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.prov_path.open("a", encoding="utf-8") as f:
            for s, p, o in triples:
                record = {
                    "s": format_term(s),
                    "p": format_term(p),
                    "o": format_term(o),
                    "source": source,
                    "at": now,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def provenance(self, s: str, p: str, o: str) -> list[dict[str, str]]:
        """Look up provenance records for one triple (canonicalized match)."""
        key = (
            format_term(parse_term(s)),
            format_term(parse_term(p)),
            format_term(parse_term(o, as_object=True)),
        )
        return self._provenance_index().get(key, [])

    def _provenance_index(self) -> dict[tuple[str, str, str], list[dict[str, str]]]:
        """All provenance records grouped by (s, p, o) in format_term form."""
        index: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        if not self.prov_path.exists():
            return index
        with self.prov_path.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                index.setdefault((rec["s"], rec["p"], rec["o"]), []).append(rec)
        return index

    def remove_triples(self, triples: list[tuple[str, str, str]]) -> int:
        removed = 0
        for s, p, o in triples:
            triple = (
                parse_term(s),
                parse_term(p),
                parse_term(o, as_object=True),
            )
            if triple in self.graph:
                self.graph.remove(triple)
                removed += 1
        if removed:
            self._closure_cache = None
            self._name_index_cache = None
            self.save()
        return removed

    # -- query ---------------------------------------------------------------

    def sparql(self, query: str) -> list[dict[str, str]] | bool:
        """Run a SPARQL SELECT/ASK query. SELECT → list of binding dicts, ASK → bool."""
        with _deadline(
            SPARQL_TIMEOUT_S,
            "SPARQL evaluation exceeded {}s — rdflib evaluates joins in pure "
            "Python and an under-constrained pattern (unbound predicates, "
            "cross joins) can enumerate billions of rows. Add concrete "
            "subjects/predicates or a LIMIT and try again.",
        ):
            result = self.graph.query(query)
            if result.type == "ASK":
                return bool(result.askAnswer)
            rows = []
            for binding in result:
                rows.append(
                    {str(var): format_term(val) for var, val in zip(result.vars, binding) if val is not None}
                )
            return rows

    def find(
        self,
        subject: str | None = None,
        predicate: str | None = None,
        obj: str | None = None,
        limit: int = 50,
        with_origin: bool = False,
    ) -> list[tuple[str, ...]]:
        """Pattern-match triples; None acts as a wildcard.

        With `with_origin`, each row carries a fourth element tagging whether
        the triple was asserted or materialized by infer() ("asserted" |
        "inferred") — only meaningful after infer() has run.
        """
        s = parse_term(subject) if subject else None
        p = parse_term(predicate) if predicate else None
        o = parse_term(obj, as_object=True) if obj else None
        out: list[tuple[str, ...]] = []
        for triple in self.graph.triples((s, p, o)):
            row = tuple(format_term(t) for t in triple)
            if with_origin:
                row += ("inferred" if triple in self._inferred_triples else "asserted",)
            out.append(row)
            if len(out) >= limit:
                break
        return out

    # -- reasoning -----------------------------------------------------------

    def closure(self) -> Graph:
        """Return the graph expanded with OWL-RL + RDFS inference.

        Cached until the next mutation; treat the returned graph as read-only.
        """
        if self._closure_cache is not None:
            return self._closure_cache
        expanded = Graph()
        for prefix, ns in _PREFIXES.items():
            expanded.bind(prefix, ns)
        for triple in self.graph:
            expanded.add(triple)
        with _deadline(
            CLOSURE_TIMEOUT_S,
            "OWL-RL closure exceeded {}s; the KB is too large or too densely "
            "axiomatized for full materialization. Use kb_sparql/kb_find "
            "against the asserted triples instead.",
        ):
            owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(expanded)
        self._closure_cache = expanded
        return expanded

    def infer(self) -> int:
        """Materialize inferred triples into the in-memory graph only.

        Deliberately never save()s: persisting closure triples to kb.ttl
        would bake provenance-less inferred facts into the on-disk KB, and
        an unsound closure (contradiction-bearing KB) would be permanent.
        The materialization lives for this KnowledgeBase instance; use
        export() if a serialized closure is explicitly wanted.

        Returns the number of new triples.
        """
        before = len(self.graph)
        expanded = self.closure()
        for triple in expanded:
            if triple not in self.graph:
                self.graph.add(triple)
                self._inferred_triples.add(triple)
        return len(self.graph) - before

    @property
    def inferred_triple_count(self) -> int:
        """How many triples in the graph were materialized by infer()."""
        return len(self._inferred_triples)

    def functional_violations(self) -> list[dict]:
        """Enumerate functional-property violations over ASSERTED triples only.

        Deliberately pre-closure: on a KB that contains contradictions,
        OWL-RL closure entails owl:sameAs between the conflicting objects of
        a functional property, and the resulting sameAs chains merge
        unrelated entities — manufacturing spurious violations that did not
        appear in what anyone actually asserted (observed on the audit KBs:
        5 injected conflicts ballooned to 95 closure-level ones). Asserted-
        only enumeration keeps every conflict attributable to concrete
        triples, each returned with its provenance records.
        """
        prov_index = self._provenance_index()
        violations: list[dict] = []
        # infer() materializes the closure into self.graph, so "asserted"
        # means "not tracked as inferred" — the sweep must stay identical
        # before and after a kb_infer call in the same session.
        inferred = self._inferred_triples
        with _deadline(
            SPARQL_TIMEOUT_S,
            "Violation sweep exceeded {}s; the KB is too large for a full "
            "functional-property scan. Use kb_find per predicate instead.",
        ):
            for prop in sorted(self.graph.subjects(RDF.type, OWL.FunctionalProperty)):
                if (prop, RDF.type, OWL.FunctionalProperty) in inferred:
                    continue
                by_subject: dict[Node, set[Node]] = {}
                for s, o in self.graph.subject_objects(prop):
                    if (s, prop, o) in inferred:
                        continue
                    by_subject.setdefault(s, set()).add(o)
                for s, objects in by_subject.items():
                    if len(objects) < 2:
                        continue
                    fs, fp = format_term(s), format_term(prop)
                    violations.append(
                        {
                            "subject": fs,
                            "predicate": fp,
                            "objects": [
                                {
                                    "object": fo,
                                    "provenance": prov_index.get((fs, fp, fo), []),
                                }
                                for fo in sorted(format_term(o) for o in objects)
                            ],
                        }
                    )
        violations.sort(key=lambda v: (v["subject"], v["predicate"]))
        return violations

    def verify_triple(self, s: str, p: str, o: str) -> VerifyResult:
        """Check one claim against the KB + inference.

        - entailed:     the triple is in the OWL-RL closure of the KB
        - contradicted: the predicate is functional and the KB entails a
                        different object, or the closure is inconsistent with it
        - unknown:      the KB has no opinion
        """
        ts = parse_term(s)
        tp = parse_term(p)
        to = parse_term(o, as_object=True)
        closure = self.closure()

        if (ts, tp, to) in closure:
            return VerifyResult("entailed", "The claim follows from the knowledge base.")

        # Functional-property conflict: KB says s p o' with o' != o.
        if (tp, RDF.type, OWL.FunctionalProperty) in closure:
            others = [x for x in closure.objects(ts, tp) if x != to]
            if others:
                have = ", ".join(format_term(x) for x in others)
                return VerifyResult(
                    "contradicted",
                    f"{format_term(tp)} is functional and the KB entails "
                    f"{format_term(ts)} {format_term(tp)} {have}.",
                )

        # owl:differentFrom-based contradiction for sameAs-style claims.
        if tp == OWL.sameAs and (ts, OWL.differentFrom, to) in closure:
            return VerifyResult("contradicted", "The KB entails these are different individuals.")

        return VerifyResult("unknown", "The KB neither entails nor contradicts the claim.")

    def _case_variants(self, term: URIRef) -> list[URIRef]:
        """IRIs in the graph that match `term` up to letter case (S/O positions)."""
        if self._name_index_cache is None:
            index: dict[str, set[URIRef]] = {}
            for s, _, o in self.graph:
                for node in (s, o):
                    if isinstance(node, URIRef):
                        index.setdefault(str(node).lower(), set()).add(node)
            self._name_index_cache = index
        return sorted(self._name_index_cache.get(str(term).lower(), ()), key=str)

    def check_answer(
        self, answer: str, start: str | None = None, relation: str | None = None
    ) -> CheckAnswerResult:
        """Deterministic FINAL-gate for QA answers (design-revision-plan §S1).

        Runs three checks over the ASSERTED graph (no closure — this is a
        surface-form and graph-shape gate, not an entailment check):

        - existence:       the answer occurs in the KB at all; fabricated and
                           miscased IDs are rejected (with the correctly-cased
                           candidates when only the casing is off).
        - type:            the answer occurs with the final-hop relation, in
                           object or subject position (the KB stores one
                           direction; questions ask both). An answer that
                           never co-occurs with the relation is the wrong
                           kind of entity for the question.
        - start_exclusion: the answer is the start entity itself, or a direct
                           one-hop neighbor of it — the dominant wrong-answer
                           mode for multi-hop questions (~93% of 3-hop
                           failures answer a direct attribute of the start).

        The verdict is the worst status across checks; the caller (the agent)
        makes the final judgment — warn means "re-derive the chain", not "wrong".
        """
        g = self.graph
        ans = parse_term(answer, as_object=True)
        checks: list[CheckOutcome] = []

        # 1. existence
        if isinstance(ans, Literal):
            exists = (None, None, ans) in g
        else:
            exists = (ans, None, None) in g or (None, None, ans) in g
        if exists:
            checks.append(
                CheckOutcome("existence", "pass", f"{format_term(ans)} occurs in the KB.")
            )
        else:
            variants = self._case_variants(ans) if isinstance(ans, URIRef) else []
            if variants:
                hint = ", ".join(format_term(v) for v in variants[:5])
                detail = (
                    f"{format_term(ans)} is not in the KB, but a differently-cased "
                    f"entity is: {hint}. Answers must match the KB's casing exactly."
                )
            else:
                detail = (
                    f"{format_term(ans)} does not occur anywhere in the KB (neither "
                    "as subject nor as object) — likely a fabricated identifier."
                )
            checks.append(CheckOutcome("existence", "reject", detail))

        # 2. type: does the answer occur with the final-hop relation?
        if relation is None:
            checks.append(CheckOutcome("type", "skipped", "No final-hop relation supplied."))
        elif not exists:
            checks.append(CheckOutcome("type", "skipped", "Answer failed the existence check."))
        else:
            rel = parse_term(relation)
            if (None, rel, None) not in g:
                checks.append(
                    CheckOutcome(
                        "type",
                        "warn",
                        f"Relation {format_term(rel)} has no triples in the KB — "
                        "check the predicate name.",
                    )
                )
            elif (None, rel, ans) in g:
                checks.append(
                    CheckOutcome(
                        "type",
                        "pass",
                        f"{format_term(ans)} occurs in object position of "
                        f"{format_term(rel)} — type-compatible with the question.",
                    )
                )
            elif not isinstance(ans, Literal) and (ans, rel, None) in g:
                checks.append(
                    CheckOutcome(
                        "type",
                        "pass",
                        f"{format_term(ans)} occurs in subject position of "
                        f"{format_term(rel)} (inverse direction) — type-compatible "
                        "if the question asks in that direction.",
                    )
                )
            else:
                checks.append(
                    CheckOutcome(
                        "type",
                        "warn",
                        f"{format_term(ans)} never occurs with {format_term(rel)} in "
                        "either position — it is probably the wrong kind of entity "
                        "for this question.",
                    )
                )

        # 3. start exclusion: one-hop-from-start answers to multi-hop questions
        if start is None:
            checks.append(
                CheckOutcome("start_exclusion", "skipped", "No start entity supplied.")
            )
        else:
            st = parse_term(start)
            if st == ans:
                checks.append(
                    CheckOutcome(
                        "start_exclusion",
                        "warn",
                        "The answer IS the start entity. For a multi-hop question "
                        "this is the returned-pivot failure mode; walk the chain "
                        "instead of echoing the question's entity.",
                    )
                )
            else:
                links = set(g.predicates(st, ans))
                if not isinstance(ans, Literal):
                    links |= set(g.predicates(ans, st))
                if links:
                    names = ", ".join(sorted(format_term(p) for p in links))
                    rel_note = (
                        " — including the final-hop relation itself"
                        if relation is not None and parse_term(relation) in links
                        else ""
                    )
                    checks.append(
                        CheckOutcome(
                            "start_exclusion",
                            "warn",
                            f"The answer is DIRECTLY linked to the start entity via "
                            f"{names}{rel_note}. For a multi-hop question, a one-hop "
                            "attribute of the start is the dominant wrong-answer "
                            "mode; re-derive the full hop chain before trusting it.",
                        )
                    )
                else:
                    checks.append(
                        CheckOutcome(
                            "start_exclusion",
                            "pass",
                            "The answer is not a direct neighbor of the start entity.",
                        )
                    )

        rank = {"reject": 2, "warn": 1}
        worst = max(rank.get(c.status, 0) for c in checks)
        return CheckAnswerResult({2: "reject", 1: "warn", 0: "pass"}[worst], checks)

    def traverse_path(
        self,
        start: str,
        relations: list[str],
        *,
        exclude_start: bool = True,
        max_terminals: int = MAX_PATH_TERMINALS,
        max_hops: int = MAX_PATH_HOPS,
    ) -> PathResult:
        """Walk a predicate chain from `start` deterministically (§S2 kb_path).

        Each hop follows its predicate in BOTH directions: MetaQA-style KBs
        store each fact once (movie -> attribute) while questions traverse
        either way ("movies starring X" is the inverse of starred_actors),
        and per-hop direction judgment is exactly where agents misstep (~93%
        of 3-hop failures answer a misread start attribute). A relation
        prefixed with '^' restricts that hop to the inverse direction, an
        escape hatch for when bidirectional fan-out is too large. Note this
        deviates from SPARQL property-path semantics, where a bare predicate
        is forward-only.

        Returns the terminal entity set plus, per hop, the actual edges
        traversed — each a verifiable KB triple labeled forward/inverse.
        With exclude_start (default), the start entity is removed from the
        TERMINAL set only (the answer convention for MetaQA-style questions:
        "other movies starring X's actors" never includes X itself) and
        reported in `excluded`; intermediate hops may still pass through the
        start, so e.g. the start's own director stays reachable at 3 hops.
        Truncation caps apply to the returned payload only — the traversal
        itself always runs to completion (under the deadline guard).
        """
        if not relations:
            raise ValueError("relations must contain at least one predicate")
        if len(relations) > max_hops:
            raise ValueError(
                f"chain of {len(relations)} hops exceeds the limit of {max_hops}; "
                "split the question into shorter chains"
            )
        start_term = parse_term(start)
        notes: list[str] = []
        hops: list[PathHop] = []
        if (start_term, None, None) not in self.graph and (
            None,
            None,
            start_term,
        ) not in self.graph:
            notes.append(
                f"start entity {format_term(start_term)} does not occur in the KB — "
                "check its spelling and casing (kb_check_answer suggests case variants)."
            )
        frontier: set[Node] = {start_term}
        with _deadline(
            SPARQL_TIMEOUT_S,
            "kb_path traversal exceeded {}s — the chain fans out too widely on "
            "this KB. Restrict hop directions with '^', or split the chain and "
            "narrow intermediate frontiers with kb_find.",
        ):
            for idx, spec in enumerate(relations, start=1):
                text = spec.strip()
                inverse_only = text.startswith("^")
                pred = parse_term(text[1:] if inverse_only else text)
                if (None, pred, None) not in self.graph:
                    notes.append(
                        f"hop {idx}: predicate {format_term(pred)} has no triples in "
                        "the KB — check the predicate name (kb_sparql SELECT DISTINCT "
                        "?p lists the valid ones)."
                    )
                edges: list[PathEdge] = []
                edges_truncated = False
                next_frontier: set[Node] = set()
                # Sorted iteration end to end: truncation must be reproducible,
                # never an artifact of set/store ordering (cf. B1p neighborhood).
                for entity in sorted(frontier, key=str):
                    if isinstance(entity, Literal):
                        continue  # literals terminate a path; they cannot be expanded
                    steps: list[tuple[Node, Node, str, Node]] = []
                    if not inverse_only:
                        steps += [
                            (entity, o, "forward", o) for o in self.graph.objects(entity, pred)
                        ]
                    steps += [
                        (s, entity, "inverse", s) for s in self.graph.subjects(pred, entity)
                    ]
                    for s_node, o_node, direction, nxt in sorted(
                        steps, key=lambda t: (str(t[0]), str(t[1]))
                    ):
                        next_frontier.add(nxt)
                        if len(edges) < MAX_PATH_EDGES_PER_HOP:
                            edges.append(
                                PathEdge(
                                    format_term(s_node),
                                    format_term(pred),
                                    format_term(o_node),
                                    direction,
                                )
                            )
                        else:
                            edges_truncated = True
                hops.append(
                    PathHop(
                        relation=spec,
                        edges=edges,
                        edges_truncated=edges_truncated,
                        frontier_size=len(next_frontier),
                    )
                )
                if edges_truncated:
                    notes.append(
                        f"hop {idx}: edge evidence truncated at "
                        f"{MAX_PATH_EDGES_PER_HOP} edges (the frontier itself is "
                        "complete; kb_verify specific edges if needed)."
                    )
                frontier = next_frontier
                if not frontier:
                    if idx < len(relations):
                        notes.append(
                            f"hop {idx}: no matching edges — traversal stopped "
                            f"before hop {idx + 1}; the chain yields no terminals."
                        )
                    else:
                        notes.append(
                            f"hop {idx}: no matching edges — the chain yields no terminals."
                        )
                    break

        excluded: list[str] = []
        if exclude_start and start_term in frontier:
            frontier = frontier - {start_term}
            excluded.append(format_term(start_term))
            notes.append(
                f"excluded the start entity {format_term(start_term)} from the "
                "terminals (exclude_start=True): a multi-hop answer set never "
                "contains the question's own entity. Pass exclude_start=false "
                "only if the question genuinely allows it."
            )
        terminals = sorted(format_term(t) for t in frontier)
        terminals_truncated = len(terminals) > max_terminals
        if terminals_truncated:
            notes.append(
                f"terminal set truncated to {max_terminals} of {len(terminals)} "
                "entities — the chain is under-constrained for this question; "
                "consider a more specific chain or '^' direction restrictions."
            )
            terminals = terminals[:max_terminals]
        return PathResult(
            terminals=terminals,
            terminals_truncated=terminals_truncated,
            excluded=excluded,
            hops=hops,
            notes=notes,
        )

    # -- export / import -------------------------------------------------------

    def export(self, dest: Path, fmt: str = "turtle") -> None:
        """Serialize the KB to `dest` (turtle | nt | xml | json-ld | n3)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.graph.serialize(destination=dest, format=fmt)

    def import_file(self, src: Path, fmt: str | None = None) -> int:
        """Merge triples from an RDF file into the KB. Returns count added."""
        before = len(self.graph)
        self.graph.parse(src, format=fmt)  # fmt=None → guess from extension
        added = len(self.graph) - before
        if added:
            self._closure_cache = None
            self._name_index_cache = None
            self.save()
        return added

    # -- meta ----------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        subjects = set(self.graph.subjects())
        predicates = set(self.graph.predicates())
        return {
            "triples": len(self.graph),
            "entities": len(subjects),
            "predicates": len(predicates),
        }
