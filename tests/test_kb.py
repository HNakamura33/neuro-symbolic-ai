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


def test_closure_cache_reused_and_invalidated(kb: KnowledgeBase):
    kb.add_triples([("ns:alice", "ns:knows", "ns:bob")])
    first = kb.closure()
    assert kb.closure() is first  # cached until the graph changes
    kb.add_triples([("ns:bob", "ns:knows", "ns:carol")])
    second = kb.closure()
    assert second is not first
    assert (parse_term("ns:bob"), parse_term("ns:knows"), parse_term("ns:carol")) in second
    kb.remove_triples([("ns:bob", "ns:knows", "ns:carol")])
    assert kb.closure() is not second


def test_functional_violations_clean_kb(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:born_in", "rdf:type", "owl:FunctionalProperty"),
            ("ns:alice", "ns:born_in", "ns:tokyo"),
            ("ns:bob", "ns:born_in", "ns:osaka"),
            ("ns:alice", "ns:likes", "ns:sushi"),  # non-functional, multi-valued OK
            ("ns:alice", "ns:likes", "ns:tea"),
        ]
    )
    assert kb.functional_violations() == []


def test_functional_violations_found_with_provenance(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:born_in", "rdf:type", "owl:FunctionalProperty"),
            ("ns:alice", "ns:born_in", "ns:tokyo"),
        ],
        source="census.txt",
    )
    kb.add_triples([("ns:alice", "ns:born_in", "ns:osaka")], source="injected")
    violations = kb.functional_violations()
    assert len(violations) == 1
    v = violations[0]
    assert (v["subject"], v["predicate"]) == ("ns:alice", "ns:born_in")
    by_object = {entry["object"]: entry["provenance"] for entry in v["objects"]}
    assert set(by_object) == {"ns:tokyo", "ns:osaka"}
    assert by_object["ns:tokyo"][0]["source"] == "census.txt"
    assert by_object["ns:osaka"][0]["source"] == "injected"


def test_functional_violations_pre_closure_no_sameas_cascade(kb: KnowledgeBase):
    # Under OWL-RL, the alice conflict entails tokyo sameAs osaka, which
    # (via the sameAs aliases) would also make carol and dave look
    # conflicted — the exact cascade that collapsed audit precision. The
    # asserted-only sweep must report the one real conflict, before and
    # after the closure is materialized.
    kb.add_triples(
        [
            ("ns:born_in", "rdf:type", "owl:FunctionalProperty"),
            ("ns:alice", "ns:born_in", "ns:tokyo"),
            ("ns:alice", "ns:born_in", "ns:osaka"),
            ("ns:carol", "ns:born_in", "ns:tokyo"),
            ("ns:dave", "ns:born_in", "ns:osaka"),
            ("ns:carol2", "owl:sameAs", "ns:carol"),
        ]
    )
    def pairs():
        return [(v["subject"], v["predicate"]) for v in kb.functional_violations()]

    assert pairs() == [("ns:alice", "ns:born_in")]
    kb.infer()  # materializing the (unsound) closure must not change the sweep
    assert pairs() == [("ns:alice", "ns:born_in")]


def test_find_tags_origin_after_infer(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:socrates", "rdf:type", "ns:Human"),
            ("ns:Human", "rdfs:subClassOf", "ns:Mortal"),
        ]
    )
    # Before inference the plain 3-tuple shape is unchanged.
    assert kb.find(subject="ns:socrates", predicate="rdf:type") == [
        ("ns:socrates", "rdf:type", "ns:Human")
    ]
    assert kb.inferred_triple_count == 0
    kb.infer()
    assert kb.inferred_triple_count > 0
    rows = kb.find(subject="ns:socrates", predicate="rdf:type", with_origin=True)
    origins = {r[2]: r[3] for r in rows}
    assert origins["ns:Human"] == "asserted"
    assert origins["ns:Mortal"] == "inferred"


def test_infer_does_not_persist_to_disk(kb: KnowledgeBase):
    """infer() is in-memory only: closure triples have no provenance and must
    never be written into kb.ttl."""
    kb.add_triples(
        [
            ("ns:socrates", "rdf:type", "ns:Human"),
            ("ns:Human", "rdfs:subClassOf", "ns:Mortal"),
        ]
    )
    on_disk_before = kb.path.read_bytes()
    added = kb.infer()
    assert added > 0
    assert kb.path.read_bytes() == on_disk_before
    # A fresh load sees only the asserted triples, not the materialization.
    reloaded = KnowledgeBase(kb.path)
    assert (
        parse_term("ns:socrates"),
        parse_term("rdf:type"),
        parse_term("ns:Mortal"),
    ) not in reloaded.graph


# -- check_answer (S1 FINAL-gate) --------------------------------------------


@pytest.fixture
def movie_kb(tmp_path: Path) -> KnowledgeBase:
    """Synthetic MetaQA-shaped graph: The_Matrix -> Keanu -> Speed -> 1994."""
    kb = KnowledgeBase(tmp_path / "movies.ttl")
    kb.add_triples(
        [
            ("ns:The_Matrix", "ns:directed_by", "ns:Lana_Wachowski"),
            ("ns:The_Matrix", "ns:release_year", "ns:1999"),
            ("ns:The_Matrix", "ns:starred_actors", "ns:Keanu_Reeves"),
            ("ns:Speed", "ns:starred_actors", "ns:Keanu_Reeves"),
            ("ns:Speed", "ns:release_year", "ns:1994"),
            ("ns:Speed", "ns:directed_by", "ns:Jan_de_Bont"),
        ]
    )
    return kb


def _by_check(result):
    return {c.check: c for c in result.checks}


def test_check_answer_all_pass(movie_kb: KnowledgeBase):
    # 3-hop: "when did the movies Keanu starred in come out" from The_Matrix.
    r = movie_kb.check_answer("ns:1994", start="ns:The_Matrix", relation="ns:release_year")
    assert r.verdict == "pass"
    assert {c.status for c in r.checks} == {"pass"}


def test_check_answer_rejects_fabricated_id(movie_kb: KnowledgeBase):
    r = movie_kb.check_answer("ns:billy_chan", start="ns:The_Matrix")
    assert r.verdict == "reject"
    by = _by_check(r)
    assert by["existence"].status == "reject"
    assert "fabricated" in by["existence"].detail
    assert by["type"].status == "skipped"  # no relation given


def test_check_answer_rejects_miscased_id_with_hint(movie_kb: KnowledgeBase):
    r = movie_kb.check_answer("ns:keanu_reeves")
    assert r.verdict == "reject"
    by = _by_check(r)
    assert by["existence"].status == "reject"
    assert "ns:Keanu_Reeves" in by["existence"].detail


def test_check_answer_type_mismatch_warns(movie_kb: KnowledgeBase):
    # A director answered to a "when was it released" question: exists in the
    # KB, but never occurs with release_year in either position.
    r = movie_kb.check_answer(
        "ns:Lana_Wachowski", start="ns:Speed", relation="ns:release_year"
    )
    assert r.verdict == "warn"
    assert _by_check(r)["type"].status == "warn"


def test_check_answer_type_pass_object_and_subject_positions(movie_kb: KnowledgeBase):
    # Object position: an actor for starred_actors.
    r = movie_kb.check_answer("ns:Keanu_Reeves", relation="ns:starred_actors")
    assert _by_check(r)["type"].status == "pass"
    # Subject position (inverse-direction question): a movie for starred_actors.
    r = movie_kb.check_answer("ns:Speed", relation="ns:starred_actors")
    assert _by_check(r)["type"].status == "pass"


def test_check_answer_unknown_relation_warns(movie_kb: KnowledgeBase):
    r = movie_kb.check_answer("ns:Keanu_Reeves", relation="ns:has_genre")
    by = _by_check(r)
    assert by["type"].status == "warn"
    assert "no triples" in by["type"].detail


def test_check_answer_warns_on_one_hop_direct_answer(movie_kb: KnowledgeBase):
    # The dominant 3-hop failure mode: answering a direct attribute of the
    # start entity (here its own director) to a multi-hop question.
    r = movie_kb.check_answer(
        "ns:Lana_Wachowski", start="ns:The_Matrix", relation="ns:directed_by"
    )
    assert r.verdict == "warn"
    by = _by_check(r)
    assert by["existence"].status == "pass"
    assert by["type"].status == "pass"  # right type, wrong provenance
    assert by["start_exclusion"].status == "warn"
    assert "ns:directed_by" in by["start_exclusion"].detail
    assert "final-hop relation itself" in by["start_exclusion"].detail


def test_check_answer_warns_on_echoed_start(movie_kb: KnowledgeBase):
    r = movie_kb.check_answer("ns:The_Matrix", start="ns:The_Matrix")
    assert r.verdict == "warn"
    assert _by_check(r)["start_exclusion"].status == "warn"


def test_check_answer_direct_link_detected_in_both_directions(movie_kb: KnowledgeBase):
    # start appears as OBJECT of the linking triple (actor -> movie question).
    r = movie_kb.check_answer("ns:Speed", start="ns:Keanu_Reeves")
    assert _by_check(r)["start_exclusion"].status == "warn"


def test_check_answer_index_invalidated_on_mutation(movie_kb: KnowledgeBase):
    assert movie_kb.check_answer("ns:trinity").verdict == "reject"  # builds the index
    movie_kb.add_triples([("ns:The_Matrix", "ns:starred_actors", "ns:Trinity")])
    r = movie_kb.check_answer("ns:trinity")
    assert "ns:Trinity" in _by_check(r)["existence"].detail


def test_sparql_deadline_interrupts_explosive_join(tmp_path, monkeypatch):
    # A three-way unconstrained join over n triples enumerates n^3 rows in
    # pure Python; without the deadline this wedges the agent session
    # (observed live on MetaQA). 2k triples -> 8e9 combinations, far past
    # any 1s budget.
    import nsai.kb as kb_mod

    kb = kb_mod.KnowledgeBase(tmp_path / "kb.ttl")
    kb.add_triples([(f"ns:s{i}", "ns:p", f"ns:o{i}") for i in range(2000)],
                   source="test")
    monkeypatch.setattr(kb_mod, "SPARQL_TIMEOUT_S", 1)
    import time
    t0 = time.monotonic()
    with pytest.raises(kb_mod.QueryTimeout, match="under-constrained"):
        kb.sparql("SELECT ?a ?b ?c WHERE { ?a ?p1 ?x . ?y ?p2 ?b . ?z ?p3 ?c }")
    assert time.monotonic() - t0 < 10
    # The KB stays usable after the interrupt.
    assert kb.sparql("ASK { ns:s0 ns:p ns:o0 }") is True


# -- traverse_path (S2 kb_path) -----------------------------------------------


def _hop_edges(hop):
    return [(e.subject, e.predicate, e.object, e.direction) for e in hop.edges]


def test_traverse_path_two_hop_forward(kb: KnowledgeBase):
    kb.add_triples(
        [
            ("ns:a", "ns:p", "ns:b"),
            ("ns:b", "ns:q", "ns:c"),
        ]
    )
    r = kb.traverse_path("ns:a", ["ns:p", "ns:q"])
    assert r.terminals == ["ns:c"]
    assert not r.terminals_truncated and r.excluded == []
    assert _hop_edges(r.hops[0]) == [("ns:a", "ns:p", "ns:b", "forward")]
    assert _hop_edges(r.hops[1]) == [("ns:b", "ns:q", "ns:c", "forward")]


def test_traverse_path_chain_with_inverse_hop(movie_kb: KnowledgeBase):
    # "when were the movies Keanu starred in released": starred_actors must be
    # walked INVERSE (the KB stores movie -> actor), then release_year forward.
    r = movie_kb.traverse_path("ns:Keanu_Reeves", ["ns:starred_actors", "ns:release_year"])
    assert r.terminals == ["ns:1994", "ns:1999"]
    # Every returned edge is a real KB triple, labeled with its direction.
    assert _hop_edges(r.hops[0]) == [
        ("ns:Speed", "ns:starred_actors", "ns:Keanu_Reeves", "inverse"),
        ("ns:The_Matrix", "ns:starred_actors", "ns:Keanu_Reeves", "inverse"),
    ]
    assert all(e.direction == "forward" for e in r.hops[1].edges)
    assert r.hops[0].frontier_size == 2


def test_traverse_path_exclude_start(movie_kb: KnowledgeBase):
    # "other movies starring The_Matrix's actors" must not answer The_Matrix.
    chain = ["ns:starred_actors", "ns:starred_actors"]
    r = movie_kb.traverse_path("ns:The_Matrix", chain)
    assert r.terminals == ["ns:Speed"]
    assert r.excluded == ["ns:The_Matrix"]
    assert any("exclude_start" in n for n in r.notes)
    # Opting out keeps the start entity in the terminals.
    r = movie_kb.traverse_path("ns:The_Matrix", chain, exclude_start=False)
    assert r.terminals == ["ns:Speed", "ns:The_Matrix"]
    assert r.excluded == []


def test_traverse_path_start_excluded_from_terminals_only(movie_kb: KnowledgeBase):
    # 3-hop through the start: exclude_start drops the start from the TERMINAL
    # set only — intermediate hops may pass through it, so the start's own
    # director stays reachable (MetaQA gold sets include such answers).
    r = movie_kb.traverse_path(
        "ns:The_Matrix", ["ns:starred_actors", "ns:starred_actors", "ns:directed_by"]
    )
    assert r.terminals == ["ns:Jan_de_Bont", "ns:Lana_Wachowski"]


def test_traverse_path_unknown_predicate_is_explicit(movie_kb: KnowledgeBase):
    r = movie_kb.traverse_path("ns:The_Matrix", ["ns:has_genre"])
    assert r.terminals == []
    assert r.hops[0].edges == [] and r.hops[0].frontier_size == 0
    assert any("ns:has_genre has no triples" in n for n in r.notes)
    assert any("no matching edges" in n for n in r.notes)


def test_traverse_path_dead_end_stops_traversal(movie_kb: KnowledgeBase):
    r = movie_kb.traverse_path("ns:The_Matrix", ["ns:has_genre", "ns:release_year"])
    assert r.terminals == []
    assert len(r.hops) == 1  # hop 2 never runs
    assert any("stopped before hop 2" in n for n in r.notes)


def test_traverse_path_inverse_marker_restricts_direction(movie_kb: KnowledgeBase):
    # '^' walks only (?, p, entity): from an actor it finds the movies...
    r = movie_kb.traverse_path("ns:Keanu_Reeves", ["^ns:starred_actors"])
    assert r.terminals == ["ns:Speed", "ns:The_Matrix"]
    # ...but from a movie (which is never an object of starred_actors) nothing,
    # while the both-directions default does find the actor.
    r = movie_kb.traverse_path("ns:The_Matrix", ["^ns:starred_actors"])
    assert r.terminals == []
    r = movie_kb.traverse_path("ns:The_Matrix", ["ns:starred_actors"])
    assert r.terminals == ["ns:Keanu_Reeves"]


def test_traverse_path_fanout_caps(kb: KnowledgeBase):
    from nsai.kb import MAX_PATH_EDGES_PER_HOP, MAX_PATH_TERMINALS

    n = MAX_PATH_EDGES_PER_HOP + 50  # 250: past both the edge and terminal caps
    kb.add_triples([("ns:hub", "ns:p", f"ns:t{i:03d}") for i in range(n)])
    r = kb.traverse_path("ns:hub", ["ns:p"])
    assert len(r.terminals) == MAX_PATH_TERMINALS
    assert r.terminals_truncated
    assert any("truncated to 100 of 250" in n_ for n_ in r.notes)
    # Edge evidence is capped separately; the frontier itself stays complete.
    assert len(r.hops[0].edges) == MAX_PATH_EDGES_PER_HOP
    assert r.hops[0].edges_truncated
    assert r.hops[0].frontier_size == n
    # Caller-supplied cap.
    r = kb.traverse_path("ns:hub", ["ns:p"], max_terminals=5)
    assert len(r.terminals) == 5 and r.terminals_truncated


def test_traverse_path_validation(movie_kb: KnowledgeBase):
    with pytest.raises(ValueError, match="at least one"):
        movie_kb.traverse_path("ns:The_Matrix", [])
    with pytest.raises(ValueError, match="exceeds the limit"):
        movie_kb.traverse_path("ns:The_Matrix", ["ns:p"] * 6)
    # Unknown start entity: traversal still returns, with an explicit note.
    r = movie_kb.traverse_path("ns:Nobody", ["ns:starred_actors"])
    assert r.terminals == []
    assert any("does not occur in the KB" in n for n in r.notes)
