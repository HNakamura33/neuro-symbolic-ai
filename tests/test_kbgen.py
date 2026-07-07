from dataclasses import asdict
from pathlib import Path

import pytest

from experiments.kbgen import Dataset, generate, inject_contradictions, write_audit_kb
from nsai.kb import KnowledgeBase


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return generate(size=120, seed=7, claims_per_label=8, qa_per_hop=4, hops=(2, 3, 4))


def test_deterministic_for_same_seed(ds: Dataset):
    again = generate(size=120, seed=7, claims_per_label=8, qa_per_hop=4, hops=(2, 3, 4))
    assert ds.triples == again.triples
    assert [asdict(c) for c in ds.claims] == [asdict(c) for c in again.claims]
    assert [asdict(q) for q in ds.qa] == [asdict(q) for q in again.qa]
    different = generate(size=120, seed=8, claims_per_label=8, qa_per_hop=4, hops=(2, 3, 4))
    assert ds.triples != different.triples


def test_label_balance_and_kinds(ds: Dataset):
    labels = {label: [c for c in ds.claims if c.label == label] for label in
              ("entailed", "contradicted", "unknown")}
    for label, claims in labels.items():
        assert len(claims) == 8, label
    # inference-only entailments are present (subclass chain and/or sameAs)
    assert any(c.kind.startswith("inferred") for c in labels["entailed"])


def test_gold_labels_agree_with_kb_verify(ds: Dataset, tmp_path: Path):
    """End-to-end: the saved KB's own verify_triple must reproduce every gold
    label (sampled per label to keep closure computations bounded)."""
    ds.save(tmp_path)
    kb = KnowledgeBase(tmp_path / "kb.ttl")
    for label in ("entailed", "contradicted", "unknown"):
        for claim in [c for c in ds.claims if c.label == label][:3]:
            r = kb.verify_triple(claim.subject, claim.predicate, claim.object)
            assert r.verdict == label, asdict(claim)


def test_qa_answers_are_unique_and_correct(ds: Dataset):
    triple_set = set(ds.triples)
    for qa in ds.qa:
        cur = qa.start
        for pred in qa.path:
            objects = [o for (s, p, o) in triple_set if s == cur and p == f"ns:{pred}"]
            assert len(objects) == 1, (qa.start, qa.path, cur, pred)
            cur = objects[0]
        assert cur == qa.answer
        assert qa.hops == len(qa.path)


def test_injected_contradictions_are_detectable_by_sparql(ds: Dataset, tmp_path: Path):
    ds.save(tmp_path)
    kb = KnowledgeBase(tmp_path / "kb.ttl")
    injected = inject_contradictions(ds, n=5, seed=1)
    assert len(injected) == 5
    kb.add_triples(injected, source="injected")
    conflicts = kb.sparql(
        "SELECT DISTINCT ?s ?p WHERE { ?p a owl:FunctionalProperty . "
        "?s ?p ?o1, ?o2 . FILTER(?o1 != ?o2) }"
    )
    found = {(row["s"], row["p"]) for row in conflicts}
    assert found == {(s, p) for s, p, _ in injected}
    # provenance separates the disagreeing sources
    s, p, o = injected[0]
    assert any(rec["source"] == "injected" for rec in kb.provenance(s, p, o))


def test_injection_goes_to_audit_copy_and_leaves_kb_clean(ds: Dataset, tmp_path: Path):
    """Injection must not mutate kb.ttl: a functional conflict entails
    owl:sameAs merges under OWL-RL, which would flip claim/QA gold labels."""
    ds.save(tmp_path)
    before = (tmp_path / "kb.ttl").read_bytes()
    injected = write_audit_kb(tmp_path, ds, n=5, seed=1)
    assert (tmp_path / "kb.ttl").read_bytes() == before
    audit = KnowledgeBase(tmp_path / "kb-audit.ttl")
    assert len(audit.graph) == len(KnowledgeBase(tmp_path / "kb.ttl").graph) + 5
    # audit copy carries provenance for injected triples under their own source
    s, p, o = injected[0]
    assert any(rec["source"] == "injected" for rec in audit.provenance(s, p, o))
    # a contradicted claim keeps its gold label on the clean KB
    claim = next(c for c in ds.claims if c.label == "contradicted")
    kb = KnowledgeBase(tmp_path / "kb.ttl")
    assert kb.verify_triple(claim.subject, claim.predicate, claim.object).verdict == "contradicted"
