"""Synthetic KB + task generator for the experiments (docs/experiment-plan.md).

Generates, from one seed, a coherent people/companies/geography world:

- KB triples with a subclass chain (Engineer/Manager < Employee < Person <
  Agent), five functional properties (born_in, reports_to, works_at, hq_in,
  located_in), sameAs aliases and differentFrom pairs
- 3-way verification claims (entailed / contradicted / unknown) for
  experiment 1 — every gold label is validated against the OWL-RL closure
  at generation time, so the dataset cannot disagree with kb_verify
- k-hop QA pairs for experiment 2 — every step predicate is functional,
  so the answer is unique by construction
- optional contradiction injection for experiment 3 — conflicting values
  for functional properties, recorded under a different provenance source.
  Injected triples go into a separate kb-audit.ttl copy; kb.ttl stays clean
  so claim/QA gold labels remain valid

Everything is driven by random.Random(seed): same arguments → identical
dataset, so conditions and runs see the same tasks.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import owlrl
from rdflib import OWL, RDF, Graph

from nsai.kb import _PREFIXES, KnowledgeBase, parse_term

Triple = tuple[str, str, str]

CLASS_CHAIN = ["Employee", "Person", "Agent"]  # leaf classes sit below Employee
LEAF_CLASSES = ["Engineer", "Manager"]
FUNCTIONAL = ["born_in", "reports_to", "works_at", "hq_in", "located_in"]

# Natural-language rendering of one predicate step, innermost-first.
PHRASES = {
    "reports_to": "the manager of {x}",
    "born_in": "the city where {x} was born",
    "works_at": "the company where {x} works",
    "hq_in": "the headquarters city of {x}",
    "located_in": "the country containing {x}",
}


@dataclass
class Claim:
    subject: str
    predicate: str
    object: str
    label: str  # entailed | contradicted | unknown
    kind: str  # explicit | inferred_subclass | inferred_same_as | ...


@dataclass
class QA:
    question: str
    start: str
    path: list[str]
    answer: str
    hops: int


@dataclass
class Dataset:
    triples: list[Triple]
    claims: list[Claim]
    qa: list[QA]
    meta: dict = field(default_factory=dict)

    def save(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        kb = KnowledgeBase(out / "kb.ttl")
        kb.add_triples(self.triples, source="synthetic-gen")
        with (out / "claims.jsonl").open("w", encoding="utf-8") as f:
            for i, c in enumerate(self.claims):
                f.write(json.dumps({"id": f"claim-{i}", **asdict(c)}, ensure_ascii=False) + "\n")
        with (out / "qa.jsonl").open("w", encoding="utf-8") as f:
            for i, q in enumerate(self.qa):
                f.write(json.dumps({"id": f"qa-{i}", **asdict(q)}, ensure_ascii=False) + "\n")
        (out / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")


# -- closure-based gold checking ------------------------------------------------


def build_graph(triples: list[Triple]) -> Graph:
    g = Graph()
    for prefix, ns in _PREFIXES.items():
        g.bind(prefix, ns)
    for s, p, o in triples:
        g.add((parse_term(s), parse_term(p), parse_term(o, as_object=True)))
    return g


def closure_of(triples: list[Triple]) -> Graph:
    g = build_graph(triples)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return g


def verdict(closure: Graph, s: str, p: str, o: str) -> str:
    """Same decision procedure as KnowledgeBase.verify_triple, on a
    precomputed closure (one closure per dataset instead of per claim)."""
    ts = parse_term(s)
    tp = parse_term(p)
    to = parse_term(o, as_object=True)
    if (ts, tp, to) in closure:
        return "entailed"
    if (tp, RDF.type, OWL.FunctionalProperty) in closure:
        if any(x != to for x in closure.objects(ts, tp)):
            return "contradicted"
    if tp == OWL.sameAs and (ts, OWL.differentFrom, to) in closure:
        return "contradicted"
    return "unknown"


# -- world generation ------------------------------------------------------------


def _schema_triples() -> list[Triple]:
    triples: list[Triple] = []
    for leaf in LEAF_CLASSES:
        triples.append((f"ns:{leaf}", "rdfs:subClassOf", f"ns:{CLASS_CHAIN[0]}"))
    for a, b in zip(CLASS_CHAIN, CLASS_CHAIN[1:]):
        triples.append((f"ns:{a}", "rdfs:subClassOf", f"ns:{b}"))
    for pred in FUNCTIONAL:
        triples.append((f"ns:{pred}", "rdf:type", "owl:FunctionalProperty"))
    return triples


def generate(
    size: int = 500,
    seed: int = 0,
    claims_per_label: int = 30,
    qa_per_hop: int = 20,
    hops: tuple[int, ...] = (2, 3, 4, 6),
) -> Dataset:
    """Generate one dataset. `size` steers (roughly) the KB triple count."""
    rng = random.Random(seed)
    n_people = max(16, size // 5)
    people = [f"person_{i}" for i in range(n_people)]
    cities = [f"city_{i}" for i in range(max(6, n_people // 3))]
    countries = [f"country_{i}" for i in range(max(3, len(cities) // 4))]
    companies = [f"company_{i}" for i in range(max(4, n_people // 5))]

    triples = _schema_triples()

    # reports_to forest: shuffle people into chains long enough for max(hops).
    chain_len = max(hops) + 2
    shuffled = people[:]
    rng.shuffle(shuffled)
    boss: dict[str, str] = {}
    for i in range(0, len(shuffled), chain_len):
        chain = shuffled[i : i + chain_len]
        for lower, upper in zip(chain, chain[1:]):
            boss[lower] = upper
            triples.append((f"ns:{lower}", "ns:reports_to", f"ns:{upper}"))

    born: dict[str, str] = {}
    works: dict[str, str] = {}
    for p in people:
        triples.append((f"ns:{p}", "rdf:type", f"ns:{rng.choice(LEAF_CLASSES)}"))
        born[p] = rng.choice(cities)
        works[p] = rng.choice(companies)
        triples.append((f"ns:{p}", "ns:born_in", f"ns:{born[p]}"))
        triples.append((f"ns:{p}", "ns:works_at", f"ns:{works[p]}"))

    hq: dict[str, str] = {c: rng.choice(cities) for c in companies}
    country: dict[str, str] = {c: rng.choice(countries) for c in cities}
    triples += [(f"ns:{c}", "ns:hq_in", f"ns:{hq[c]}") for c in companies]
    triples += [(f"ns:{c}", "ns:located_in", f"ns:{country[c]}") for c in cities]

    # sameAs aliases for ~10% of people; differentFrom pairs among the rest.
    aliased = rng.sample(people, max(2, n_people // 10))
    for p in aliased:
        triples.append((f"ns:{p}_alias", "owl:sameAs", f"ns:{p}"))
    non_aliased = [p for p in people if p not in aliased]
    diff_pairs = []
    for _ in range(max(2, n_people // 10)):
        a, b = rng.sample(non_aliased, 2)
        diff_pairs.append((a, b))
        triples.append((f"ns:{a}", "owl:differentFrom", f"ns:{b}"))
        triples.append((f"ns:{b}", "owl:differentFrom", f"ns:{a}"))

    triples = list(dict.fromkeys(triples))
    closure = closure_of(triples)
    maps = {"reports_to": boss, "born_in": born, "works_at": works, "hq_in": hq, "located_in": country}

    claims = _make_claims(rng, closure, triples, people, cities, countries, aliased, diff_pairs, born, claims_per_label)
    qa = _make_qa(rng, maps, people, hops, qa_per_hop)

    # Count what was ACTUALLY generated, not what the generator aims for:
    # e.g. at large sizes diff_pairs can hit the per-label budget and
    # functional_conflict claims drop to zero — meta must reflect that.
    claim_labels = Counter(c.label for c in claims)
    claim_kinds = Counter(c.kind for c in claims)

    meta = {
        "seed": seed,
        "size_requested": size,
        "triples": len(triples),
        "people": n_people,
        "claims": len(claims),
        "claim_labels": dict(sorted(claim_labels.items())),
        "claim_kinds": dict(sorted(claim_kinds.items())),
        "qa": len(qa),
        "hops": list(hops),
    }
    return Dataset(triples=triples, claims=claims, qa=qa, meta=meta)


def _make_claims(
    rng: random.Random,
    closure: Graph,
    triples: list[Triple],
    people: list[str],
    cities: list[str],
    countries: list[str],
    aliased: list[str],
    diff_pairs: list[tuple[str, str]],
    born: dict[str, str],
    per_label: int,
) -> list[Claim]:
    """Sample claims per label; every gold label is checked against the closure."""
    claims: list[Claim] = []

    def add(s: str, p: str, o: str, label: str, kind: str) -> bool:
        if verdict(closure, s, p, o) != label:
            return False
        claims.append(Claim(s, p, o, label, kind))
        return True

    data = [t for t in triples if t[1].startswith("ns:")]

    # entailed: half explicit, half only derivable by inference
    for s, p, o in rng.sample(data, min(per_label // 2, len(data))):
        add(s, p, o, "entailed", "explicit")
    pool = people[:]
    rng.shuffle(pool)
    upper_classes = CLASS_CHAIN  # never asserted directly on individuals
    for p in pool:
        if len([c for c in claims if c.label == "entailed"]) >= per_label:
            break
        if rng.random() < 0.5 or not aliased:
            add(f"ns:{p}", "rdf:type", f"ns:{rng.choice(upper_classes)}", "entailed", "inferred_subclass")
        else:
            a = rng.choice(aliased)
            add(f"ns:{a}_alias", "ns:born_in", f"ns:{born[a]}", "entailed", "inferred_same_as")

    # contradicted: functional conflicts + sameAs-vs-differentFrom
    pool = people[:]
    rng.shuffle(pool)
    for p in pool:
        if len([c for c in claims if c.label == "contradicted"]) >= per_label - len(diff_pairs):
            break
        wrong = rng.choice([c for c in cities if c != born[p]])
        add(f"ns:{p}", "ns:born_in", f"ns:{wrong}", "contradicted", "functional_conflict")
    for a, b in diff_pairs:
        if len([c for c in claims if c.label == "contradicted"]) >= per_label:
            break
        add(f"ns:{a}", "owl:sameAs", f"ns:{b}", "contradicted", "different_from")

    # unknown: plausible relations the KB is silent about (non-functional preds)
    while len([c for c in claims if c.label == "unknown"]) < per_label:
        p = rng.choice(people)
        if rng.random() < 0.5:
            add(f"ns:{p}", "ns:visited", f"ns:{rng.choice(cities)}", "unknown", "silent")
        else:
            q = rng.choice([x for x in people if x != p])
            add(f"ns:{p}", "ns:knows", f"ns:{q}", "unknown", "silent")

    rng.shuffle(claims)
    return claims


def _make_qa(
    rng: random.Random,
    maps: dict[str, dict[str, str]],
    people: list[str],
    hops: tuple[int, ...],
    per_hop: int,
) -> list[QA]:
    qa: list[QA] = []
    for k in hops:
        # terminal patterns: managers up the chain, then attribute lookups
        patterns = [["reports_to"] * (k - 1) + ["born_in"]]
        if k >= 2:
            patterns.append(["reports_to"] * (k - 2) + ["works_at", "hq_in"])
        if k >= 3:
            patterns.append(["reports_to"] * (k - 3) + ["works_at", "hq_in", "located_in"])
            patterns.append(["reports_to"] * (k - 2) + ["born_in", "located_in"])
        seen: set[tuple[str, tuple[str, ...]]] = set()
        attempts = 0
        while len([q for q in qa if q.hops == k]) < per_hop and attempts < per_hop * 60:
            attempts += 1
            path = rng.choice(patterns)
            start = rng.choice(people)
            if (start, tuple(path)) in seen:
                continue
            cur = start
            try:
                for pred in path:
                    cur = maps[pred][cur]
            except KeyError:  # ran off the top of a reports_to chain
                continue
            seen.add((start, tuple(path)))
            phrase = start
            for pred in path:
                phrase = PHRASES[pred].format(x=phrase)
            question = f"Which entity is {phrase}?"
            qa.append(QA(question=question, start=f"ns:{start}", path=path, answer=f"ns:{cur}", hops=k))
    rng.shuffle(qa)
    return qa


# -- contradiction injection (experiment 3) --------------------------------------


def inject_contradictions(dataset: Dataset, n: int, seed: int = 0) -> list[Triple]:
    """Pick n functional data triples and return conflicting counterparts.

    The caller stores them under a different provenance source; the pair
    (original, injected) then violates the functional property and is
    detectable by a SPARQL sweep. Only one conflict per (subject, predicate).
    """
    rng = random.Random(seed)
    functional_preds = {f"ns:{p}" for p in ("born_in", "works_at", "hq_in", "located_in")}
    candidates = [t for t in dataset.triples if t[1] in functional_preds]
    rng.shuffle(candidates)
    objects_by_pred: dict[str, list[str]] = {}
    for s, p, o in candidates:
        objects_by_pred.setdefault(p, []).append(o)
    injected: list[Triple] = []
    used: set[tuple[str, str]] = set()
    for s, p, o in candidates:
        if len(injected) >= n:
            break
        if (s, p) in used:
            continue
        alternatives = [x for x in set(objects_by_pred[p]) if x != o]
        if not alternatives:
            continue
        used.add((s, p))
        injected.append((s, p, rng.choice(alternatives)))
    return injected


def write_audit_kb(out: Path, dataset: Dataset, n: int, seed: int = 0) -> list[Triple]:
    """Copy kb.ttl to kb-audit.ttl and inject n contradictions there.

    Injection must never touch kb.ttl: under OWL-RL a functional-property
    conflict entails owl:sameAs between the two objects, which merges
    entities and silently flips claim/QA gold labels.
    """
    injected = inject_contradictions(dataset, n, seed=seed)
    shutil.copy(out / "kb.ttl", out / "kb-audit.ttl")
    prov = out / "kb.prov.jsonl"
    if prov.exists():
        shutil.copy(prov, out / "kb-audit.prov.jsonl")
    kb = KnowledgeBase(out / "kb-audit.ttl")
    kb.add_triples(injected, source="injected")
    with (out / "injected.jsonl").open("w", encoding="utf-8") as f:
        for s, p, o in injected:
            f.write(json.dumps({"subject": s, "predicate": p, "object": o}) + "\n")
    return injected


# -- CLI --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic experiment dataset")
    ap.add_argument("--out", type=Path, required=True, help="Output directory")
    ap.add_argument("--size", type=int, default=500, help="Approximate KB triple count")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--claims-per-label", type=int, default=100)
    ap.add_argument("--qa-per-hop", type=int, default=50)
    ap.add_argument("--inject", type=int, default=0, help="Also inject N contradictions")
    args = ap.parse_args()

    ds = generate(
        size=args.size,
        seed=args.seed,
        claims_per_label=args.claims_per_label,
        qa_per_hop=args.qa_per_hop,
    )
    ds.save(args.out)
    if args.inject:
        injected = write_audit_kb(args.out, ds, args.inject, seed=args.seed)
        print(f"injected {len(injected)} contradictions into kb-audit.ttl")
    print(json.dumps(ds.meta, indent=2))


if __name__ == "__main__":
    main()
