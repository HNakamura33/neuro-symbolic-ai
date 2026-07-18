"""Public-benchmark converters for the experiment harness (docs/experiment-plan.md).

Converts benchmark distributions into the dataset directory layout produced by
`experiments.kbgen.Dataset.save` and consumed by `experiments.harness`:

    out/
      kb.ttl          facts as Turtle (ns:/rdf:/rdfs:/owl: prefixes)
      qa.jsonl        {id, question, start, answer (CURIE), hops}
      claims.jsonl    {id, subject, predicate, object, label, kind}
      meta.json       provenance, counts, exclusion reasons, mappings
      entities.json   reversible CURIE -> original surface-name mapping

Subcommands
-----------
metaqa       MetaQA (kb.txt `subject|relation|object`, per-hop qa_*.txt files)
             -> kb.ttl + qa.jsonl. Multi-answer questions are dropped for
             exact-match grading; the exclusion counts land in meta.json.
proofwriter  ProofWriter OWA meta-stage jsonl -> kb.ttl + claims.jsonl.
             Only the OWL2-RL-mappable subset: positive type/attribute facts
             (-> rdf:type), positive relation facts, and single-antecedent
             positive "X is A -> X is B" rules (-> rdfs:subClassOf). Items
             whose theory contains anything else (negation, multi-antecedent,
             grounded or relational rules) are skipped and the exclusion
             counts + reasons are recorded in meta.json.
perturb      Contamination probe (plan section "汚染対策" item 3): rewrite any
             dataset dir so every entity CURIE gets a random opaque name
             (seeded, bijective). Predicates and rdf:/rdfs:/owl: schema terms
             and literals are untouched; qa/claims gold fields are renamed
             with the same mapping so gold labels are preserved; question
             surface text is rewritten best-effort. The mapping is stored in
             meta.json under "perturbation".

Downloads: this module never fetches anything — point --src at local copies.
  MetaQA:      https://github.com/yuyuz/MetaQA
  ProofWriter: https://allenai.org/data/proofwriter

Examples:
  uv run python -m experiments.benchloader metaqa \
      --src data/raw/metaqa --out data/metaqa --split test --sample 200 --seed 42
  uv run python -m experiments.benchloader proofwriter \
      --src data/raw/proofwriter/OWA/depth-2/meta-test.jsonl --out data/pw-d2
  uv run python -m experiments.benchloader perturb \
      --src data/metaqa --out data/metaqa-perturbed --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

import owlrl
from rdflib import OWL, RDF, Graph, URIRef

from nsai.kb import _PREFIXES, NS, KnowledgeBase, parse_term

Triple = tuple[str, str, str]

# Characters allowed in a CURIE local name by nsai.kb._CURIE_RE.
_LOCAL_BAD = re.compile(r"[^\w.-]+")


def sanitize_local(name: str) -> str:
    """Turn an arbitrary surface name into a valid `ns:` local name."""
    s = _LOCAL_BAD.sub("_", name.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "x"


class CurieMapper:
    """Original surface name -> unique `ns:` CURIE; collision-safe, reversible.

    `used` may be shared between mappers (e.g. entities and relations) so two
    different surface names can never sanitize onto the same CURIE.
    """

    def __init__(self, used: set[str] | None = None):
        self.by_name: dict[str, str] = {}
        self.used = used if used is not None else set()

    def curie(self, name: str) -> str:
        if name in self.by_name:
            return self.by_name[name]
        base = sanitize_local(name)
        local, i = base, 2
        while local in self.used:
            local = f"{base}_{i}"
            i += 1
        self.used.add(local)
        curie = f"ns:{local}"
        self.by_name[name] = curie
        return curie

    def mapping(self) -> dict[str, str]:
        """CURIE -> original surface name (invertible: CURIEs are unique)."""
        return {curie: name for name, curie in self.by_name.items()}


def _write_kb(out: Path, triples: list[Triple]) -> int:
    """Write kb.ttl via KnowledgeBase for format parity with kbgen."""
    out.mkdir(parents=True, exist_ok=True)
    kb_path = out / "kb.ttl"
    if kb_path.exists():  # KnowledgeBase merges with an existing file
        kb_path.unlink()
    prov = kb_path.with_suffix(".prov.jsonl")
    if prov.exists():
        prov.unlink()
    kb = KnowledgeBase(kb_path)
    kb.add_triples(list(dict.fromkeys(triples)))
    return len(kb.graph)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _closure(triples: list[Triple]) -> Graph:
    g = Graph()
    for prefix, ns in _PREFIXES.items():
        g.bind(prefix, ns)
    for s, p, o in triples:
        g.add((parse_term(s), parse_term(p), parse_term(o, as_object=True)))
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return g


def _closure_contradicts(closure: Graph, s, p, o) -> bool:
    """Mirror of KnowledgeBase.verify_triple's 'contradicted' conditions,
    evaluated against a pre-computed closure graph. A converted KB that can
    never satisfy these (e.g. positive-only output with no functional
    properties) cannot reproduce a 'contradicted' gold label."""
    if (p, RDF.type, OWL.FunctionalProperty) in closure and any(
        x != o for x in closure.objects(s, p)
    ):
        return True
    return p == OWL.sameAs and (s, OWL.differentFrom, o) in closure


# -- MetaQA -----------------------------------------------------------------------

_BRACKET = re.compile(r"\[([^\]]+)\]")


def _metaqa_kb_file(src: Path) -> Path:
    for cand in (src / "kb.txt", src / "kb" / "kb.txt"):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"MetaQA kb.txt not found under {src} (tried kb.txt, kb/kb.txt)")


def _metaqa_qa_file(src: Path, hop: int, split: str) -> Path | None:
    d = src / f"{hop}-hop"
    for cand in (d / f"qa_{split}.txt", d / "vanilla" / f"qa_{split}.txt"):
        if cand.exists():
            return cand
    return None


def convert_metaqa(
    src: Path, out: Path, split: str = "test", sample: int | None = None, seed: int = 0,
    exclude: Path | None = None,
) -> dict:
    """MetaQA distribution dir -> dataset dir (kb.ttl + qa.jsonl).

    Only single-answer questions are kept (exact-match grading); the number of
    multi-answer questions dropped per hop is recorded in meta.json.

    ``exclude`` names an existing dataset's qa.jsonl whose questions must not
    appear in the new sample — the held-out guarantee: a sample drawn with a
    fresh seed would otherwise overlap the development set by chance.
    """
    excluded: set[tuple[int, str]] = set()
    if exclude is not None:
        for line in exclude.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                excluded.add((rec["hops"], rec["question"]))
    used: set[str] = set()
    ents = CurieMapper(used)
    rels = CurieMapper(used)

    triples: list[Triple] = []
    kb_lines_skipped = 0
    for line in _metaqa_kb_file(src).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            kb_lines_skipped += 1
            continue
        s, r, o = (p.strip() for p in parts)
        triples.append((ents.curie(s), rels.curie(r), ents.curie(o)))

    rng = random.Random(seed)
    qa_records: list[dict] = []
    per_hop: dict[str, dict] = {}
    for hop in (1, 2, 3):
        qa_file = _metaqa_qa_file(src, hop, split)
        if qa_file is None:
            per_hop[str(hop)] = {"found": False}
            continue
        kept: list[dict] = []
        total = dropped_multi = dropped_malformed = 0
        for line in qa_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            if "\t" not in line:
                dropped_malformed += 1
                continue
            question, answer_field = line.rstrip("\n").split("\t", 1)
            answers = [a.strip() for a in answer_field.split("|") if a.strip()]
            if len(answers) != 1:
                dropped_multi += 1
                continue
            m = _BRACKET.search(question)
            start = ents.curie(m.group(1)) if m else None
            kept.append(
                {
                    "question": _BRACKET.sub(lambda mm: mm.group(1), question).strip(),
                    "start": start,
                    "answer": ents.curie(answers[0]),
                    "hops": hop,
                }
            )
        excluded_here = 0
        if excluded:
            before = len(kept)
            kept = [r for r in kept if (r["hops"], r["question"]) not in excluded]
            excluded_here = before - len(kept)
        sampled = len(kept)
        if sample is not None and len(kept) > sample:
            kept = rng.sample(kept, sample)
            sampled = len(kept)
        for i, rec in enumerate(kept):
            qa_records.append({"id": f"metaqa-{hop}hop-{i}", **rec})
        per_hop[str(hop)] = {
            "found": True,
            "total": total,
            "multi_answer_dropped": dropped_multi,
            "malformed_dropped": dropped_malformed,
            "single_answer_kept": total - dropped_multi - dropped_malformed,
            "written": sampled,
        }
        if exclude is not None:
            per_hop[str(hop)]["excluded"] = excluded_here
    if not any(h.get("found") for h in per_hop.values()):
        raise FileNotFoundError(f"no qa_{split}.txt found under {src}/{{1,2,3}}-hop")

    kb_triples = _write_kb(out, triples)
    _write_jsonl(out / "qa.jsonl", qa_records)
    (out / "entities.json").write_text(
        json.dumps({"entities": ents.mapping(), "relations": rels.mapping()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    meta = {
        "benchmark": "metaqa",
        "source": str(src),
        "split": split,
        "seed": seed,
        "sample_per_hop": sample,
        "excluded_from": str(exclude) if exclude else None,
        "kb_triples": kb_triples,
        "kb_lines_skipped": kb_lines_skipped,
        "entities": len(ents.by_name),
        "relations": len(rels.by_name),
        "qa_written": len(qa_records),
        "per_hop": per_hop,
        "entity_map": "entities.json",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


# -- ProofWriter --------------------------------------------------------------------

# Assumed meta-stage representation grammar (validate against the real files):
#   fact: ("Anne" "is" "kind" "+")           4-tuple: subject predicate object polarity
#   rule: ((("someone" "is" "kind" "+")) -> ("someone" "is" "nice" "+"))
_PW_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|->|\(|\)')
_PW_VARS = {"someone", "something", "anyone", "anything"}
_PW_LABELS = {"true": "entailed", "false": "contradicted", "unknown": "unknown"}
_PW_FLIP = {"entailed": "contradicted", "contradicted": "entailed", "unknown": "unknown"}

Fact = tuple[str, str, str, str]


def _pw_sexp(text: str):
    """Parse a ProofWriter representation string into nested lists of strings."""
    tokens = _PW_TOKEN.findall(text)
    if not tokens:
        raise ValueError(f"no tokens in representation: {text!r}")
    pos = 0

    def parse():
        nonlocal pos
        tok = tokens[pos]
        if tok != "(":
            pos += 1
            return tok[1:-1] if tok.startswith('"') else tok
        pos += 1
        items = []
        while pos < len(tokens) and tokens[pos] != ")":
            items.append(parse())
        if pos >= len(tokens):
            raise ValueError(f"unbalanced parens: {text!r}")
        pos += 1
        return items

    node = parse()
    if pos != len(tokens):
        raise ValueError(f"trailing tokens: {text!r}")
    return node


def _pw_fact(node) -> Fact | None:
    if isinstance(node, list) and len(node) == 4 and all(isinstance(x, str) for x in node):
        return tuple(node)  # type: ignore[return-value]
    return None


def _pw_rule(node) -> tuple[list[Fact], Fact] | None:
    """-> (antecedent facts, consequent fact), tolerating one nesting level."""
    if not isinstance(node, list) or "->" not in node:
        return None
    i = node.index("->")
    lhs, rhs = node[:i], node[i + 1 :]
    if len(rhs) != 1:
        return None
    consequent = _pw_fact(rhs[0])
    if consequent is None:
        return None
    if len(lhs) == 1 and isinstance(lhs[0], list) and _pw_fact(lhs[0]) is None:
        lhs = lhs[0]  # antecedent group: ((f1 f2 ...))
    antecedents = [_pw_fact(a) for a in lhs]
    if not antecedents or any(f is None for f in antecedents):
        return None
    return antecedents, consequent  # type: ignore[return-value]


def _pw_fact_triple(fact: Fact, ent) -> Triple:
    s, p, o, _pol = fact
    if p.strip().lower() == "is":
        return (ent(s), "rdf:type", ent(o))
    return (ent(s), f"ns:{sanitize_local(p)}", ent(o))


def _pw_theory_triples(item: dict, prefix: str) -> tuple[list[Triple] | None, str | None]:
    """Map one item's theory to triples, or return (None, exclusion_reason)."""

    def ent(name: str) -> str:  # per-item namespace so theories cannot interact
        return f"ns:{prefix}{sanitize_local(name)}"

    triples: list[Triple] = []
    for t in (item.get("triples") or {}).values():
        repr_ = t.get("representation")
        if not repr_:
            return None, "no_representation"
        try:
            fact = _pw_fact(_pw_sexp(repr_))
        except ValueError:
            fact = None
        if fact is None:
            return None, "unparseable_fact"
        if fact[3] != "+":
            return None, "negated_fact"
        triples.append(_pw_fact_triple(fact, ent))

    for r in (item.get("rules") or {}).values():
        repr_ = r.get("representation")
        if not repr_:
            return None, "no_representation"
        try:
            rule = _pw_rule(_pw_sexp(repr_))
        except ValueError:
            rule = None
        if rule is None:
            return None, "unparseable_rule"
        antecedents, consequent = rule
        if len(antecedents) > 1:
            return None, "multi_antecedent"
        (asubj, apred, aobj, apol) = antecedents[0]
        (csubj, cpred, cobj, cpol) = consequent
        if apol != "+" or cpol != "+":
            return None, "negation"
        if apred.strip().lower() != "is" or cpred.strip().lower() != "is":
            return None, "relational_rule"
        if asubj.strip().lower() != csubj.strip().lower():
            return None, "unbound_consequent"
        if asubj.strip().lower() not in _PW_VARS:
            return None, "grounded_rule"
        triples.append((ent(aobj), "rdfs:subClassOf", ent(cobj)))
    return triples, None


def convert_proofwriter(src: Path, out: Path, validate: bool = True) -> dict:
    """ProofWriter OWA meta-stage jsonl -> dataset dir (kb.ttl + claims.jsonl).

    Label mapping: proved -> entailed, disproved -> contradicted,
    unknown -> unknown. Negated question statements are mapped to their
    positive triple with the label flipped (true<->false), so gold stays
    consistent with kb_verify on the positive claim.

    With validate=True (default) every converted claim is checked against the
    OWL-RL closure of the converted KB; claims whose gold label the closure
    cannot reproduce are dropped and counted in meta.json.
    """

    def ent(prefix: str, name: str) -> str:
        return f"ns:{prefix}{sanitize_local(name)}"

    items = [
        json.loads(line)
        for line in src.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    excluded_items: Counter[str] = Counter()
    excluded_questions: Counter[str] = Counter()
    all_triples: list[Triple] = []
    claims: list[dict] = []
    included = 0

    for idx, item in enumerate(items):
        item_id = str(item.get("id", f"item{idx}"))
        prefix = sanitize_local(item_id) + "_"
        triples, reason = _pw_theory_triples(item, prefix)
        if reason is not None:
            excluded_items[reason] += 1
            continue
        included += 1
        all_triples += triples

        for qid, q in (item.get("questions") or {}).items():
            repr_ = q.get("representation")
            if not repr_:
                excluded_questions["no_representation"] += 1
                continue
            try:
                fact = _pw_fact(_pw_sexp(repr_))
            except ValueError:
                fact = None
            if fact is None:
                excluded_questions["unparseable_question"] += 1
                continue
            answer = str(q.get("answer")).strip().lower()
            label = _PW_LABELS.get(answer)
            if label is None:
                excluded_questions["unrecognized_answer"] += 1
                continue
            if fact[3] == "-":  # negated statement -> positive triple, flipped label
                label = _PW_FLIP[label]
            s, p, o = _pw_fact_triple(fact, lambda n: ent(prefix, n))
            depth = q.get("QDep", q.get("depth"))
            claims.append(
                {
                    "id": f"{item_id}-{qid}",
                    "subject": s,
                    "predicate": p,
                    "object": o,
                    "label": label,
                    "kind": f"depth-{depth}" if depth is not None else "proofwriter",
                    "item": item_id,
                    "text": q.get("question"),
                }
            )

    validation: dict = {"enabled": validate}
    if validate and claims:
        closure = _closure(all_triples)
        kept = []
        for c in claims:
            s = parse_term(c["subject"])
            p = parse_term(c["predicate"])
            o = parse_term(c["object"], as_object=True)
            entailed = (s, p, o) in closure
            contradicted = _closure_contradicts(closure, s, p, o)
            if c["label"] == "entailed" and not entailed:
                excluded_questions["entailed_not_reproduced"] += 1
                continue
            if c["label"] == "contradicted" and not contradicted:
                excluded_questions["contradicted_not_reproduced"] += 1
                continue
            if c["label"] == "unknown" and entailed:
                excluded_questions["unknown_but_entailed"] += 1
                continue
            if c["label"] == "unknown" and contradicted:
                excluded_questions["unknown_but_contradicted"] += 1
                continue
            kept.append(c)
        validation["claims_dropped"] = len(claims) - len(kept)
        claims = kept

    kb_triples = _write_kb(out, all_triples)
    _write_jsonl(out / "claims.jsonl", claims)
    meta = {
        "benchmark": "proofwriter",
        "source": str(src),
        "items_total": len(items),
        "items_included": included,
        "items_excluded": sum(excluded_items.values()),
        "item_exclusion_reasons": dict(excluded_items),
        "claims_written": len(claims),
        "question_exclusion_reasons": dict(excluded_questions),
        "labels": dict(Counter(c["label"] for c in claims)),
        "kb_triples": kb_triples,
        "validation": validation,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


# -- perturbation (entity renaming) ---------------------------------------------------


def _load_surface_map(src: Path) -> dict[str, str]:
    """CURIE -> human-readable surface name from entities.json, if present."""
    path = src / "entities.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("entities"), dict):
        return data["entities"]
    return data if isinstance(data, dict) else {}


def perturb(src: Path, out: Path, seed: int = 0) -> dict:
    """Rename every entity in a dataset dir to a random opaque name.

    - entities = `ns:` URIs occurring in subject/object position that are never
      used as a predicate (so classes are renamed too; ns: predicates and all
      rdf:/rdfs:/owl: terms and literals stay put)
    - the mapping is seeded and bijective; the same mapping is applied to
      subject/object/answer/start fields of qa.jsonl / claims.jsonl, so gold
      labels are preserved
    - question surface text is rewritten best-effort using entities.json
      surfaces (falling back to CURIE local names)
    """
    kb_path = src / "kb.ttl"
    if not kb_path.exists():
        raise FileNotFoundError(f"{kb_path} not found")
    g = Graph()
    g.parse(kb_path, format="turtle")

    ns_base = str(NS)
    predicates = {p for p in g.predicates() if isinstance(p, URIRef)}
    entities = sorted(
        {
            term
            for s, _p, o in g
            for term in (s, o)
            if isinstance(term, URIRef) and str(term).startswith(ns_base) and term not in predicates
        }
    )

    rng = random.Random(seed)
    width = max(4, len(str(len(entities) * 10)))
    numbers = rng.sample(range(10**width), len(entities))
    uri_map = {old: NS[f"ent_{n:0{width}d}"] for old, n in zip(entities, numbers)}
    curie_map = {
        f"ns:{str(old)[len(ns_base):]}": f"ns:{str(new)[len(ns_base):]}"
        for old, new in uri_map.items()
    }

    out.mkdir(parents=True, exist_ok=True)
    new_g = Graph()
    for prefix, ns in _PREFIXES.items():
        new_g.bind(prefix, ns)
    for s, p, o in g:
        new_g.add((uri_map.get(s, s), p, uri_map.get(o, o)))
    new_g.serialize(destination=out / "kb.ttl", format="turtle")

    # Best-effort surface rewriting for question text.
    surface_map = _load_surface_map(src)
    surface_to_new: dict[str, str] = {}
    for old_curie, new_curie in curie_map.items():
        new_local = new_curie[len("ns:"):]
        surface_to_new.setdefault(old_curie[len("ns:"):], new_local)
        surface = surface_map.get(old_curie)
        if surface:
            surface_to_new.setdefault(surface, new_local)
    if surface_to_new:
        pattern = re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(s) for s in sorted(surface_to_new, key=len, reverse=True))
            + r")(?!\w)"
        )

        def rewrite_text(text: str) -> str:
            return pattern.sub(lambda m: surface_to_new[m.group(0)], text)
    else:
        def rewrite_text(text: str) -> str:
            return text

    files_written = ["kb.ttl"]
    for name in ("qa.jsonl", "claims.jsonl"):
        path = src / name
        if not path.exists():
            continue
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            for key, value in rec.items():
                if not isinstance(value, str):
                    continue
                if value in curie_map:
                    rec[key] = curie_map[value]
                elif key in ("question", "text"):
                    rec[key] = rewrite_text(value)
            records.append(rec)
        _write_jsonl(out / name, records)
        files_written.append(name)

    meta = {}
    if (src / "meta.json").exists():
        meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    meta["perturbation"] = {
        "seed": seed,
        "renamed": len(curie_map),
        "files": files_written,
        "mapping": curie_map,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


# -- CLI --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="benchloader",
        description="Convert public benchmarks into harness dataset directories",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("metaqa", help="MetaQA distribution dir -> kb.ttl + qa.jsonl")
    m.add_argument("--src", type=Path, required=True, help="MetaQA distribution directory")
    m.add_argument("--out", type=Path, required=True, help="Output dataset directory")
    m.add_argument("--split", default="test", choices=("train", "dev", "test"))
    m.add_argument("--sample", type=int, default=None, help="Sample N questions per hop")
    m.add_argument("--seed", type=int, default=0)
    m.add_argument("--exclude", type=Path, default=None,
                   help="qa.jsonl of an existing dataset; its questions are "
                        "excluded before sampling (held-out set construction)")

    p = sub.add_parser("proofwriter", help="ProofWriter OWA meta jsonl -> kb.ttl + claims.jsonl")
    p.add_argument("--src", type=Path, required=True, help="ProofWriter meta-stage .jsonl file")
    p.add_argument("--out", type=Path, required=True, help="Output dataset directory")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip OWL-RL closure validation of converted gold labels")

    r = sub.add_parser("perturb", help="Rename entities to random opaque names (seeded)")
    r.add_argument("--src", type=Path, required=True, help="Existing dataset directory")
    r.add_argument("--out", type=Path, required=True, help="Output dataset directory")
    r.add_argument("--seed", type=int, default=0)

    args = ap.parse_args(argv)
    if args.cmd == "metaqa":
        meta = convert_metaqa(args.src, args.out, split=args.split,
                              sample=args.sample, seed=args.seed,
                              exclude=args.exclude)
    elif args.cmd == "proofwriter":
        meta = convert_proofwriter(args.src, args.out, validate=not args.no_validate)
    else:
        meta = perturb(args.src, args.out, seed=args.seed)
        meta = {**meta, "perturbation": {k: v for k, v in meta["perturbation"].items()
                                         if k != "mapping"}}
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
