from pathlib import Path

import pytest

from nsai.kb import KnowledgeBase, TermParseError, parse_term


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(tmp_path / "kb.ttl")


def test_add_and_find(kb: KnowledgeBase):
    added = kb.add_triples([("ns:alice", "ns:knows", "ns:bob")])
    assert added == 1
    assert kb.find(subject="ns:alice") == [("ns:alice", "ns:knows", "ns:bob")]
    # duplicate is a no-op
    assert kb.add_triples([("ns:alice", "ns:knows", "ns:bob")]) == 0


def test_persistence(tmp_path: Path):
    path = tmp_path / "kb.ttl"
    KnowledgeBase(path).add_triples([("ns:a", "ns:p", '"hello"')])
    reloaded = KnowledgeBase(path)
    assert reloaded.stats()["triples"] == 1


def test_sparql_select(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:alice", "ns:age", "30"),
            ("ns:bob", "ns:age", "25"),
        ]
    )
    rows = kb.sparql(
        "SELECT ?who WHERE { ?who <http://nsai.local/ns#age> ?age . FILTER(?age > 28) }"
    )
    assert rows == [{"who": "ns:alice"}]


def test_sparql_ask(kb: KnowledgeBase):
    kb.add_triples([("ns:x", "ns:p", "ns:y")])
    assert kb.sparql("ASK { <http://nsai.local/ns#x> ?p ?o }") is True
    assert kb.sparql("ASK { <http://nsai.local/ns#zzz> ?p ?o }") is False


def test_rdfs_inference_subclass(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:socrates", "rdf:type", "ns:Human"),
            ("ns:Human", "rdfs:subClassOf", "ns:Mortal"),
        ]
    )
    result = kb.verify_triple("ns:socrates", "rdf:type", "ns:Mortal")
    assert result.verdict == "entailed"


def test_functional_property_contradiction(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:born_in", "rdf:type", "owl:FunctionalProperty"),
            ("ns:alice", "ns:born_in", "ns:tokyo"),
        ]
    )
    result = kb.verify_triple("ns:alice", "ns:born_in", "ns:osaka")
    assert result.verdict == "contradicted"


def test_unknown_claim(kb: KnowledgeBase):
    result = kb.verify_triple("ns:alice", "ns:likes", "ns:sushi")
    assert result.verdict == "unknown"


def test_provenance_roundtrip(kb: KnowledgeBase):
    kb.add_triples([("ns:alice", "ns:born_in", "ns:tokyo")], source="census.txt")
    records = kb.provenance("ns:alice", "ns:born_in", "ns:tokyo")
    assert len(records) == 1
    assert records[0]["source"] == "census.txt"
    # unlogged triple has no provenance
    kb.add_triples([("ns:x", "ns:p", "ns:y")])
    assert kb.provenance("ns:x", "ns:p", "ns:y") == []


def test_export_import(kb: KnowledgeBase, tmp_path: Path):
    kb.add_triples([("ns:a", "ns:p", "ns:b"), ("ns:c", "ns:p", "ns:d")])
    out = tmp_path / "export.ttl"
    kb.export(out)

    other = KnowledgeBase(tmp_path / "other.ttl")
    other.add_triples([("ns:a", "ns:p", "ns:b")])  # overlap
    added = other.import_file(out)
    assert added == 1  # only the non-duplicate merged
    assert other.stats()["triples"] == 2


def test_parse_term_errors():
    with pytest.raises(TermParseError):
        parse_term("")
    with pytest.raises(TermParseError):
        parse_term("bogus:thing")


def test_parse_literals():
    assert parse_term("42", as_object=True).toPython() == 42
    assert parse_term('"Tokyo"', as_object=True).toPython() == "Tokyo"
    assert parse_term("true", as_object=True).toPython() is True
