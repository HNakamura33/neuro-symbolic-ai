"""Offline fixture tests for experiments/benchloader.py.

The MetaQA and ProofWriter fixtures are tiny hand-written files in the
documented distribution formats; no benchmark download is required.
"""

import json
from pathlib import Path

import pytest

from experiments.benchloader import (
    CurieMapper,
    convert_metaqa,
    convert_proofwriter,
    main,
    perturb,
    sanitize_local,
)
from nsai.kb import KnowledgeBase


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# -- sanitization -------------------------------------------------------------------


def test_sanitize_local():
    assert sanitize_local("Robert Downey Jr.") == "Robert_Downey_Jr."
    assert sanitize_local("Jane O'Brien") == "Jane_O_Brien"
    assert sanitize_local("  a  &  b ") == "a_b"
    assert sanitize_local("!!!") == "x"
    assert sanitize_local("1994") == "1994"


def test_curie_mapper_is_collision_safe_and_reversible():
    m = CurieMapper()
    a = m.curie("A&B")
    b = m.curie("A+B")  # sanitizes to the same local name
    assert a == "ns:A_B" and b == "ns:A_B_2"
    assert m.curie("A&B") == a  # stable for repeated names
    mapping = m.mapping()
    assert mapping == {"ns:A_B": "A&B", "ns:A_B_2": "A+B"}


# -- MetaQA --------------------------------------------------------------------------


@pytest.fixture()
def metaqa_src(tmp_path: Path) -> Path:
    src = tmp_path / "metaqa-raw"
    src.mkdir()
    (src / "kb.txt").write_text(
        "Movie One|directed_by|John Smith\n"
        "Movie One|starred_actors|Jane O'Brien\n"
        "Movie One|starred_actors|Bob Roe\n"
        "Movie One|release_year|1994\n"
        "Movie Two|directed_by|John Smith\n"
        "Movie Two|release_year|1997\n",
        encoding="utf-8",
    )
    # 1-hop uses the vanilla/ layout, 2-hop the flat layout; 3-hop is absent.
    one = src / "1-hop" / "vanilla"
    one.mkdir(parents=True)
    (one / "qa_test.txt").write_text(
        "who directed [Movie One]\tJohn Smith\n"
        "who acted in [Movie One]\tJane O'Brien|Bob Roe\n"
        "when was [Movie Two] released\t1997\n",
        encoding="utf-8",
    )
    two = src / "2-hop"
    two.mkdir()
    (two / "qa_test.txt").write_text(
        "which movies were directed by the director of [Movie One]\tMovie Two\n",
        encoding="utf-8",
    )
    return src


def test_metaqa_kb_and_qa_conversion(metaqa_src: Path, tmp_path: Path):
    out = tmp_path / "out"
    meta = convert_metaqa(metaqa_src, out)

    kb = KnowledgeBase(out / "kb.ttl")
    assert len(kb.graph) == 6 == meta["kb_triples"]
    assert kb.find("ns:Movie_One", "ns:directed_by") == [
        ("ns:Movie_One", "ns:directed_by", "ns:John_Smith")
    ]
    # entity map is reversible, including punctuation-heavy names
    entities = json.loads((out / "entities.json").read_text(encoding="utf-8"))
    assert entities["entities"]["ns:Jane_O_Brien"] == "Jane O'Brien"
    assert entities["relations"]["ns:directed_by"] == "directed_by"

    qa = read_jsonl(out / "qa.jsonl")
    assert len(qa) == 3  # multi-answer question dropped
    by_hop = {r["hops"]: [q for q in qa if q["hops"] == r["hops"]] for r in qa}
    assert len(by_hop[1]) == 2 and len(by_hop[2]) == 1
    first = next(q for q in qa if q["question"].startswith("who directed"))
    assert first["question"] == "who directed Movie One"  # brackets stripped
    assert first["start"] == "ns:Movie_One"
    assert first["answer"] == "ns:John_Smith"
    # every answer CURIE is resolvable back to a surface name
    assert all(q["answer"] in entities["entities"] for q in qa)


def test_metaqa_exclude_builds_disjoint_heldout_sample(metaqa_src: Path, tmp_path: Path):
    dev_out = tmp_path / "dev"
    convert_metaqa(metaqa_src, dev_out)
    held_out = tmp_path / "heldout"
    meta = convert_metaqa(metaqa_src, held_out, exclude=dev_out / "qa.jsonl")
    # every single-answer question is in dev, so the held-out sample is empty
    assert read_jsonl(held_out / "qa.jsonl") == []
    assert meta["excluded_from"] == str(dev_out / "qa.jsonl")
    assert meta["per_hop"]["1"]["excluded"] == 2
    assert meta["per_hop"]["1"]["written"] == 0
    # partial exclusion: drop one dev question, only it survives into held-out
    dev_qa = read_jsonl(dev_out / "qa.jsonl")
    kept_back = dev_qa[0]
    (dev_out / "qa.jsonl").write_text(
        "\n".join(json.dumps(r) for r in dev_qa[1:]) + "\n", encoding="utf-8"
    )
    meta = convert_metaqa(metaqa_src, tmp_path / "heldout2", exclude=dev_out / "qa.jsonl")
    qa = read_jsonl(tmp_path / "heldout2" / "qa.jsonl")
    assert [q["question"] for q in qa] == [kept_back["question"]]


def test_metaqa_meta_records_exclusions_and_missing_hops(metaqa_src: Path, tmp_path: Path):
    meta = convert_metaqa(metaqa_src, tmp_path / "out")
    assert meta["per_hop"]["1"] == {
        "found": True,
        "total": 3,
        "multi_answer_dropped": 1,
        "malformed_dropped": 0,
        "single_answer_kept": 2,
        "written": 2,
    }
    assert meta["per_hop"]["3"] == {"found": False}


def test_metaqa_sampling_is_seeded_and_deterministic(metaqa_src: Path, tmp_path: Path):
    out1, out2 = tmp_path / "a", tmp_path / "b"
    convert_metaqa(metaqa_src, out1, sample=1, seed=3)
    convert_metaqa(metaqa_src, out2, sample=1, seed=3)
    qa1, qa2 = read_jsonl(out1 / "qa.jsonl"), read_jsonl(out2 / "qa.jsonl")
    assert qa1 == qa2
    assert sum(q["hops"] == 1 for q in qa1) == 1
    assert sum(q["hops"] == 2 for q in qa1) == 1
    # full conversion is byte-deterministic too
    out3, out4 = tmp_path / "c", tmp_path / "d"
    convert_metaqa(metaqa_src, out3)
    convert_metaqa(metaqa_src, out4)
    assert (out3 / "qa.jsonl").read_bytes() == (out4 / "qa.jsonl").read_bytes()
    assert (out3 / "entities.json").read_bytes() == (out4 / "entities.json").read_bytes()


def test_metaqa_missing_kb_raises(tmp_path: Path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(FileNotFoundError):
        convert_metaqa(src, tmp_path / "out")


# -- ProofWriter ----------------------------------------------------------------------


def pw_item(item_id: str, triples: dict, rules: dict, questions: dict) -> dict:
    return {"id": item_id, "triples": triples, "rules": rules, "questions": questions}


def fact(s: str, p: str, o: str, pol: str = "+") -> str:
    return f'("{s}" "{p}" "{o}" "{pol}")'


def rule(antecedents: list[str], consequent: str) -> str:
    return f'(({" ".join(antecedents)}) -> {consequent})'


@pytest.fixture()
def proofwriter_src(tmp_path: Path) -> Path:
    items = [
        # mappable: attribute facts, a relation fact, one subclass rule
        pw_item(
            "item1",
            {
                "triple1": {"text": "Anne is kind.", "representation": fact("Anne", "is", "kind")},
                "triple2": {"text": "Bob is big.", "representation": fact("Bob", "is", "big")},
                "triple3": {"text": "Anne likes Bob.", "representation": fact("Anne", "likes", "Bob")},
            },
            {
                "rule1": {
                    "text": "If someone is kind then they are nice.",
                    "representation": rule([fact("someone", "is", "kind")], fact("someone", "is", "nice")),
                }
            },
            {
                "Q1": {"question": "Anne is nice.", "answer": "true", "QDep": 1,
                       "representation": fact("Anne", "is", "nice")},
                "Q2": {"question": "Bob is nice.", "answer": "unknown", "QDep": 0,
                       "representation": fact("Bob", "is", "nice")},
                "Q3": {"question": "Anne is not nice.", "answer": "false", "QDep": 1,
                       "representation": fact("Anne", "is", "nice", "-")},
                "Q4": {"question": "Bob is not kind.", "answer": "unknown", "QDep": 0,
                       "representation": fact("Bob", "is", "kind", "-")},
                # contradicted gold: the positive-only converted KB can never
                # reproduce it (no functional properties / differentFrom), so
                # validation must drop it instead of letting it through.
                "Q5": {"question": "Anne is big.", "answer": "false", "QDep": 1,
                       "representation": fact("Anne", "is", "big")},
            },
        ),
        # excluded: negated rule consequent
        pw_item(
            "item2",
            {"triple1": {"representation": fact("Cat", "is", "kind")}},
            {"rule1": {"representation": rule([fact("someone", "is", "kind")],
                                              fact("someone", "is", "happy", "-"))}},
            {"Q1": {"question": "Cat is happy.", "answer": "false",
                    "representation": fact("Cat", "is", "happy")}},
        ),
        # excluded: multi-antecedent rule
        pw_item(
            "item3",
            {"triple1": {"representation": fact("Dan", "is", "big")}},
            {"rule1": {"representation": rule(
                [fact("someone", "is", "big"), fact("someone", "is", "kind")],
                fact("someone", "is", "nice"))}},
            {"Q1": {"question": "Dan is nice.", "answer": "unknown",
                    "representation": fact("Dan", "is", "nice")}},
        ),
        # excluded: grounded (non-variable) rule
        pw_item(
            "item4",
            {"triple1": {"representation": fact("Erin", "is", "kind")}},
            {"rule1": {"representation": rule([fact("Erin", "is", "kind")],
                                              fact("Erin", "is", "nice"))}},
            {"Q1": {"question": "Erin is nice.", "answer": "true",
                    "representation": fact("Erin", "is", "nice")}},
        ),
        # mappable theory, but the gold label is not reproducible -> validation drop
        pw_item(
            "item5",
            {"triple1": {"representation": fact("Fred", "is", "big")}},
            {},
            {"Q1": {"question": "Fred is nice.", "answer": "true",
                    "representation": fact("Fred", "is", "nice")}},
        ),
    ]
    src = tmp_path / "meta-test.jsonl"
    src.write_text("\n".join(json.dumps(i) for i in items) + "\n", encoding="utf-8")
    return src


def test_proofwriter_mapping_and_labels(proofwriter_src: Path, tmp_path: Path):
    out = tmp_path / "out"
    meta = convert_proofwriter(proofwriter_src, out)

    kb = KnowledgeBase(out / "kb.ttl")
    # theory of item1 + item5, per-item entity namespacing
    assert kb.find("ns:item1_Anne", "rdf:type") == [("ns:item1_Anne", "rdf:type", "ns:item1_kind")]
    assert kb.find("ns:item1_kind", "rdfs:subClassOf") == [
        ("ns:item1_kind", "rdfs:subClassOf", "ns:item1_nice")
    ]
    assert kb.find("ns:item1_Anne", "ns:likes") == [("ns:item1_Anne", "ns:likes", "ns:item1_Bob")]

    claims = {c["id"]: c for c in read_jsonl(out / "claims.jsonl")}
    assert claims["item1-Q1"]["label"] == "entailed"  # proved, via the subclass rule
    assert claims["item1-Q1"]["kind"] == "depth-1"
    assert claims["item1-Q2"]["label"] == "unknown"
    # negated statements map to the positive triple with a flipped label
    assert claims["item1-Q3"]["label"] == "entailed"
    assert claims["item1-Q3"]["object"] == "ns:item1_nice"
    assert claims["item1-Q4"]["label"] == "unknown"
    assert set(claims) == {"item1-Q1", "item1-Q2", "item1-Q3", "item1-Q4"}

    assert meta["items_included"] == 2
    assert meta["item_exclusion_reasons"] == {
        "negation": 1,
        "multi_antecedent": 1,
        "grounded_rule": 1,
    }
    assert meta["question_exclusion_reasons"]["entailed_not_reproduced"] == 1
    assert meta["question_exclusion_reasons"]["contradicted_not_reproduced"] == 1
    assert meta["validation"] == {"enabled": True, "claims_dropped": 2}
    assert meta["labels"] == {"entailed": 2, "unknown": 2}


def test_proofwriter_gold_agrees_with_kb_verify(proofwriter_src: Path, tmp_path: Path):
    out = tmp_path / "out"
    convert_proofwriter(proofwriter_src, out)
    kb = KnowledgeBase(out / "kb.ttl")
    for c in read_jsonl(out / "claims.jsonl"):
        verdict = kb.verify_triple(c["subject"], c["predicate"], c["object"]).verdict
        assert verdict == c["label"], c


def test_proofwriter_no_validate_keeps_unreproducible_claims(proofwriter_src: Path, tmp_path: Path):
    meta = convert_proofwriter(proofwriter_src, tmp_path / "out", validate=False)
    assert meta["validation"] == {"enabled": False}
    assert meta["claims_written"] == 6  # item1-Q5 and item5-Q1 kept


# -- perturbation -----------------------------------------------------------------------


@pytest.fixture()
def claims_dataset(tmp_path: Path) -> Path:
    """Tiny hand-built dataset: functional property, subclass chain, claims."""
    src = tmp_path / "claims-ds"
    src.mkdir()
    kb = KnowledgeBase(src / "kb.ttl")
    kb.add_triples(
        [
            ("ns:born_in", "rdf:type", "owl:FunctionalProperty"),
            ("ns:alice", "ns:born_in", "ns:tokyo"),
            ("ns:alice", "rdf:type", "ns:Engineer"),
            ("ns:Engineer", "rdfs:subClassOf", "ns:Person"),
        ]
    )
    claims = [
        {"id": "c0", "subject": "ns:alice", "predicate": "ns:born_in", "object": "ns:tokyo",
         "label": "entailed", "kind": "explicit"},
        {"id": "c1", "subject": "ns:alice", "predicate": "rdf:type", "object": "ns:Person",
         "label": "entailed", "kind": "inferred_subclass"},
        {"id": "c2", "subject": "ns:alice", "predicate": "ns:born_in", "object": "ns:osaka",
         "label": "contradicted", "kind": "functional_conflict"},
        {"id": "c3", "subject": "ns:alice", "predicate": "ns:knows", "object": "ns:tokyo",
         "label": "unknown", "kind": "silent"},
    ]
    with (src / "claims.jsonl").open("w", encoding="utf-8") as f:
        for c in claims:
            f.write(json.dumps(c) + "\n")
    (src / "meta.json").write_text(json.dumps({"benchmark": "fixture"}), encoding="utf-8")
    return src


def test_perturb_renames_entities_but_not_predicates_or_schema(claims_dataset: Path, tmp_path: Path):
    out = tmp_path / "out"
    meta = perturb(claims_dataset, out, seed=5)
    mapping = meta["perturbation"]["mapping"]

    # bijective, covers exactly the non-predicate ns: terms (classes included)
    assert set(mapping) == {"ns:alice", "ns:tokyo", "ns:Engineer", "ns:Person"}
    assert len(set(mapping.values())) == len(mapping)
    assert all(v.startswith("ns:ent_") for v in mapping.values())

    kb = KnowledgeBase(out / "kb.ttl")
    # structure is preserved under the mapping; predicates and owl: terms intact
    assert kb.find(mapping["ns:alice"], "ns:born_in") == [
        (mapping["ns:alice"], "ns:born_in", mapping["ns:tokyo"])
    ]
    assert kb.find("ns:born_in", "rdf:type") == [
        ("ns:born_in", "rdf:type", "owl:FunctionalProperty")
    ]
    assert kb.find(mapping["ns:Engineer"], "rdfs:subClassOf") == [
        (mapping["ns:Engineer"], "rdfs:subClassOf", mapping["ns:Person"])
    ]
    # no original entity name survives in the turtle
    ttl = (out / "kb.ttl").read_text(encoding="utf-8")
    for old in ("alice", "tokyo", "Engineer", "Person"):
        assert old not in ttl


def test_perturb_preserves_claim_gold_labels(claims_dataset: Path, tmp_path: Path):
    out = tmp_path / "out"
    meta = perturb(claims_dataset, out, seed=5)
    mapping = meta["perturbation"]["mapping"]
    kb = KnowledgeBase(out / "kb.ttl")
    claims = {c["id"]: c for c in read_jsonl(out / "claims.jsonl")}
    assert len(claims) == 4
    for c in claims.values():
        assert kb.verify_triple(c["subject"], c["predicate"], c["object"]).verdict == c["label"], c
    assert claims["c0"]["subject"] == mapping["ns:alice"]
    assert claims["c0"]["predicate"] == "ns:born_in"  # predicates unchanged
    assert claims["c1"]["object"] == mapping["ns:Person"]
    assert claims["c2"]["object"] == "ns:osaka"  # not a KB entity -> untouched


def test_perturb_is_deterministic_per_seed(claims_dataset: Path, tmp_path: Path):
    m1 = perturb(claims_dataset, tmp_path / "o1", seed=5)["perturbation"]["mapping"]
    m2 = perturb(claims_dataset, tmp_path / "o2", seed=5)["perturbation"]["mapping"]
    m3 = perturb(claims_dataset, tmp_path / "o3", seed=6)["perturbation"]["mapping"]
    assert m1 == m2
    assert m1 != m3


def test_perturb_metaqa_pipeline_rewrites_questions(metaqa_src: Path, tmp_path: Path):
    dataset = tmp_path / "ds"
    convert_metaqa(metaqa_src, dataset)
    out = tmp_path / "out"
    meta = perturb(dataset, out, seed=1)
    mapping = meta["perturbation"]["mapping"]

    qa = read_jsonl(out / "qa.jsonl")
    assert len(qa) == 3
    first = next(q for q in qa if q["question"].startswith("who directed"))
    # answer/start CURIEs renamed with the KB mapping -> gold preserved
    assert first["answer"] == mapping["ns:John_Smith"]
    assert first["start"] == mapping["ns:Movie_One"]
    # surface form replaced in the question text with the opaque local name
    assert "Movie One" not in first["question"]
    assert mapping["ns:Movie_One"][len("ns:"):] in first["question"]
    # the renamed KB still answers the question
    kb = KnowledgeBase(out / "kb.ttl")
    assert kb.find(first["start"], "ns:directed_by") == [
        (first["start"], "ns:directed_by", first["answer"])
    ]


# -- CLI ----------------------------------------------------------------------------


def test_cli_end_to_end(metaqa_src: Path, tmp_path: Path, capsys):
    dataset = tmp_path / "ds"
    main(["metaqa", "--src", str(metaqa_src), "--out", str(dataset),
          "--sample", "1", "--seed", "3"])
    assert (dataset / "kb.ttl").exists()
    assert (dataset / "qa.jsonl").exists()
    assert (dataset / "meta.json").exists()

    perturbed = tmp_path / "ds-perturbed"
    main(["perturb", "--src", str(dataset), "--out", str(perturbed), "--seed", "9"])
    assert (perturbed / "kb.ttl").exists()
    printed = capsys.readouterr().out
    assert '"mapping"' not in printed  # CLI prints a summary, not the full mapping
    meta = json.loads((perturbed / "meta.json").read_text(encoding="utf-8"))
    assert meta["perturbation"]["seed"] == 9
    assert "mapping" in meta["perturbation"]  # ...but meta.json keeps it
    assert meta["benchmark"] == "metaqa"  # source meta carried through
