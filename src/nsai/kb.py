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
        """Materialize inferred triples into the KB. Returns number of new triples."""
        before = len(self.graph)
        expanded = self.closure()
        for triple in expanded:
            if triple not in self.graph:
                self.graph.add(triple)
                self._inferred_triples.add(triple)
        added = len(self.graph) - before
        if added:
            self.save()
        return added

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
