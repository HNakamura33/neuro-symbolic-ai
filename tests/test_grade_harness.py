from pathlib import Path

import pytest

from experiments.grade import (
    accuracy,
    mcnemar_exact,
    paired_bootstrap,
    summarize,
    three_way_metrics,
)
from experiments.harness import (
    build_condition_options,
    parse_final,
    parse_shard,
    render_prompt,
)
from experiments.kbgen import generate
from nsai.tools import ALLOWED_TOOL_NAMES


def _rec(task_id, gold, pred, **extra):
    return {"task_id": task_id, "task_type": "claims", "condition": "X",
            "gold": gold, "pred": pred, "correct": gold == pred,
            "cost_usd": 0.01, "seconds": 1.0, "tool_calls": {}, "run": 0, **extra}


def test_three_way_metrics_and_false_verification():
    records = [
        _rec("t1", "entailed", "entailed"),
        _rec("t2", "contradicted", "entailed"),   # hallucinated verification
        _rec("t3", "unknown", "unknown"),
        _rec("t4", "unknown", "entailed"),        # hallucinated verification
    ]
    m = three_way_metrics(records)
    assert m["per_label"]["entailed"]["precision"] == pytest.approx(1 / 3)
    assert m["per_label"]["entailed"]["recall"] == 1.0
    assert m["false_verification_rate"] == pytest.approx(2 / 3)
    assert accuracy(records) == 0.5


def test_mcnemar_counts_discordant_pairs():
    a = [_rec("t1", "e", "e"), _rec("t2", "e", "e"), _rec("t3", "e", "x")]
    b = [_rec("t1", "e", "x"), _rec("t2", "e", "e"), _rec("t3", "e", "e")]
    m = mcnemar_exact(a, b)
    assert (m["b"], m["c"]) == (1, 1)
    assert m["p_value"] == 1.0
    identical = mcnemar_exact(a, a)
    assert identical["p_value"] == 1.0 and identical["b"] == identical["c"] == 0


def test_paired_bootstrap_detects_a_clear_difference():
    a = [1.0] * 30
    b = [0.0] * 30
    r = paired_bootstrap(a, b, n_resamples=2000, seed=0)
    assert r["mean_diff"] == 1.0
    assert r["ci95"][0] > 0.9
    # A perfectly consistent maximal effect must be highly significant
    # (the pre-fix implementation returned p = 1.0 here).
    assert r["p_value"] < 0.01
    same = paired_bootstrap(a, a, n_resamples=2000, seed=0)
    assert same["mean_diff"] == 0.0
    # No effect: every centered resample is at least as extreme as 0.
    assert same["p_value"] == 1.0


def test_paired_bootstrap_p_value_tracks_effect_consistency():
    # 28/30 positive: still a clear effect, small p.
    a = [1.0] * 28 + [0.0] * 2
    b = [0.0] * 28 + [1.0] * 2
    strong = paired_bootstrap(a, b, n_resamples=2000, seed=0)
    assert strong["p_value"] < 0.05
    # 16/30 vs 14/30: weak effect, p must NOT be significant.
    a = [1.0] * 16 + [0.0] * 14
    b = [1.0] * 14 + [0.0] * 16
    weak = paired_bootstrap(a, b, n_resamples=2000, seed=0)
    assert weak["p_value"] > 0.05


def test_summarize_audit_f1_zero_is_reported():
    # precision 0.0 is a real worst-case score, not "unmeasured": f1 must be
    # present and 0.0 (the pre-fix truthiness gate silently dropped the key).
    records = [{**_rec("a0", ["x"], ["y"]), "task_type": "audit",
                "precision": 0.0, "recall": 0.0}]
    s = summarize(records)
    assert s["mean_precision"] == 0.0
    assert s["f1"] == 0.0


def test_summarize_handles_coding4a_records():
    # coding4a records carry passed (older files lack correct/pred/task_type);
    # aggregation must not KeyError and accuracy is the pass rate.
    records = [
        {"task_id": "gcd", "condition": "plain", "model": "sonnet", "passed": True,
         "n_cases": 5, "cases_failed": [], "cost_usd": 0.01, "seconds": 3.0,
         "tool_calls": {"Edit": 1}, "cases_skipped": [], "run": 0},
        {"task_id": "sqrt", "condition": "plain", "model": "sonnet", "passed": False,
         "n_cases": 4, "cases_failed": [2], "cost_usd": 0.02, "seconds": 4.0,
         "tool_calls": {}, "cases_skipped": [], "run": 0},
    ]
    s = summarize(records)
    assert s["n"] == 2
    assert s["accuracy"] == 0.5
    assert s["unparsed"] == 0
    assert accuracy(records) == 0.5


def test_summarize_reports_hops_breakdown():
    records = [
        {**_rec("q1", "ns:a", "ns:a"), "task_type": "qa", "hops": 2},
        {**_rec("q2", "ns:b", "ns:x"), "task_type": "qa", "hops": 4},
    ]
    s = summarize(records)
    assert s["accuracy_by_hops"] == {2: 1.0, 4: 0.0}


def test_parse_final():
    assert parse_final("thinking...\nFINAL: entailed") == "entailed"
    assert parse_final("FINAL: ns:tokyo\nwait no\nFINAL: ns:osaka.") == "ns:osaka"
    assert parse_final("no answer line") is None


def test_parse_shard_and_exact_partition():
    assert parse_shard("0/1") == (0, 1)
    assert parse_shard("2/3") == (2, 3)
    for bad in ("3/3", "-1/3", "0/0", "banana", "1"):
        with pytest.raises(SystemExit):
            parse_shard(bad)
    # The N shards must partition the task list exactly: every task in
    # exactly one shard, so concatenated shard outputs equal a serial run.
    tasks = [f"t{i}" for i in range(23)]
    for n in (1, 2, 3, 4, 7):
        shards = [tasks[k::n] for k in range(n)]
        assert sorted(t for s in shards for t in s) == sorted(tasks)


def test_gold_normalized_like_predictions():
    # Entities can legitimately end in "." (sanitize_local keeps it); the
    # gold side must go through the same trailing-punctuation strip as
    # parse_final or such answers can never be graded correct.
    from experiments.harness import normalize_answer

    gold = "ns:Robert_Downey_Jr."
    pred = parse_final("FINAL: ns:Robert_Downey_Jr.")
    assert pred == normalize_answer(gold)


def test_render_prompt_embeds_facts_only_for_b1():
    task = {"id": "claim-0", "subject": "ns:a", "predicate": "ns:p", "object": "ns:b",
            "label": "entailed", "kind": "explicit"}
    ttl = "@prefix ns: <http://nsai.local/ns#> ."
    b1 = render_prompt(task, "claims", "B1", ttl)
    assert "KNOWLEDGE BASE" in b1 and "@prefix" in b1
    for cond in ("B0", "C1", "C2"):
        assert "@prefix" not in render_prompt(task, "claims", cond, ttl)
    assert "symbolic tools" in render_prompt(task, "claims", "C2", None)


def test_condition_options(tmp_path: Path):
    ds = generate(size=100, seed=0, claims_per_label=2, qa_per_hop=2, hops=(2,))
    ds.save(tmp_path)
    kb_path = tmp_path / "kb.ttl"

    b0 = build_condition_options(kb_path, "B0", None)
    assert b0.allowed_tools == [] and not b0.mcp_servers

    c1 = build_condition_options(kb_path, "C1", None)
    assert c1.allowed_tools == ALLOWED_TOOL_NAMES
    assert not c1.agents

    c2 = build_condition_options(kb_path, "C2", None)
    assert "Task" in c2.allowed_tools
    assert c2.agents


def test_parse_final_pairs():
    from experiments.harness import parse_final_pairs

    text = ("I found conflicts.\n"
            "FINAL: ns:p1|ns:born_in; ns:c2|ns:hq_in ; ns:p1|ns:born_in")
    assert parse_final_pairs(text) == ["ns:c2|ns:hq_in", "ns:p1|ns:born_in"]
    assert parse_final_pairs("no final line") is None


def test_audit_task_loading_and_prompt(tmp_path: Path):
    import json

    from experiments.harness import load_tasks

    (tmp_path / "injected.jsonl").write_text(
        json.dumps({"subject": "ns:a", "predicate": "ns:born_in", "object": "ns:x"}) + "\n"
        + json.dumps({"subject": "ns:b", "predicate": "ns:hq_in", "object": "ns:y"}) + "\n",
        encoding="utf-8",
    )
    tasks = load_tasks(tmp_path, "audit")
    assert tasks == [{"id": "audit-0", "gold_pairs": ["ns:a|ns:born_in", "ns:b|ns:hq_in"]}]
    prompt = render_prompt(tasks[0], "audit", "C1", None)
    assert "FINAL:" in prompt and "symbolic tools" in prompt
    # prompt_rev 4: the tool conditions get the kb_violations pointer...
    assert "kb_violations" in prompt
    assert "kb_violations" in render_prompt(tasks[0], "audit", "C2", None)
    b1 = render_prompt(tasks[0], "audit", "B1", "ttl-content-here")
    assert "ttl-content-here" in b1
    # ...but the tool-less baselines must not be told about a tool they lack.
    assert "kb_violations" not in b1
    assert "kb_violations" not in render_prompt(tasks[0], "audit", "B2", None)


# ---------------------------------------------------------------------------
# B1p oracle-BFS retrieval
# ---------------------------------------------------------------------------

# Chain a -> b -> c -> d plus a hub h attached to b: BFS distances from a are
# b=1, {c,h}=2, d=3. Five triples total.
B1P_TTL = """\
@prefix ns: <http://nsai.local/ns#> .
ns:a ns:p ns:b .
ns:b ns:p ns:c .
ns:c ns:p ns:d .
ns:b ns:q ns:h .
ns:h ns:q ns:hh .
"""


@pytest.fixture()
def b1p_index(tmp_path: Path):
    from experiments.harness import load_neighborhood_index

    kb = tmp_path / "kb.ttl"
    kb.write_text(B1P_TTL, encoding="utf-8")
    return load_neighborhood_index(kb)


def _uri(name: str):
    from rdflib import URIRef

    return URIRef(f"http://nsai.local/ns#{name}")


def test_neighborhood_radius_semantics(b1p_index):
    from experiments.harness import neighborhood

    triples, adj = b1p_index
    sel1, trunc1 = neighborhood(triples, adj, _uri("a"), 1, 100)
    assert len(sel1) == 1 and not trunc1              # only a->b
    sel2, _ = neighborhood(triples, adj, _uri("a"), 2, 100)
    assert len(sel2) == 3                             # + b->c, b->h
    sel3, _ = neighborhood(triples, adj, _uri("a"), 3, 100)
    assert len(sel3) == 5                             # whole graph
    # Distance order: the radius-1 triple always precedes radius-2 ones.
    assert sel2[0] == sel1[0]


def test_neighborhood_truncation_keeps_closest(b1p_index):
    from experiments.harness import neighborhood

    triples, adj = b1p_index
    sel, truncated = neighborhood(triples, adj, _uri("a"), 3, 2)
    assert truncated and len(sel) == 2
    assert sel[0] == (_uri("a"), _uri("p"), _uri("b"))  # closest survives


def test_neighborhood_facts_extras_and_coverage(b1p_index):
    from experiments.harness import neighborhood_facts

    triples, adj = b1p_index
    task = {"id": "qa-0", "question": "?", "start": "ns:a",
            "answer": "ns:d", "hops": 3}
    ttl, extras = neighborhood_facts(triples, adj, task, None, 100)
    assert extras == {"b1p_radius": 3, "b1p_triples": 5,
                      "b1p_truncated": False, "b1p_gold_in_context": True}
    assert "@prefix ns:" in ttl and "ns:d" in ttl
    # Radius from the task's hop count: at hops=1 the gold d is unreachable.
    ttl1, extras1 = neighborhood_facts(triples, adj, {**task, "hops": 1}, None, 100)
    assert extras1["b1p_radius"] == 1
    assert extras1["b1p_gold_in_context"] is False
    assert "ns:d" not in ttl1.replace("@prefix", "")
    # Explicit --b1p-radius override wins over the hop count.
    _, extras_fixed = neighborhood_facts(triples, adj, {**task, "hops": 1}, 3, 100)
    assert extras_fixed["b1p_radius"] == 3
    assert extras_fixed["b1p_gold_in_context"] is True


def test_render_prompt_b1p_embeds_facts_and_hint():
    task = {"id": "qa-0", "question": "who?", "start": "ns:a",
            "answer": "ns:d", "hops": 2}
    ttl = "@prefix ns: <http://nsai.local/ns#> ."
    prompt = render_prompt(task, "qa", "B1p", ttl)
    assert "KNOWLEDGE BASE" in prompt and "@prefix" in prompt
    assert "retrieved around the question entity" in prompt


def test_b1p_condition_options_are_toolless(tmp_path: Path):
    kb = tmp_path / "kb.ttl"
    kb.write_text(B1P_TTL, encoding="utf-8")
    options = build_condition_options(kb, "B1p", None)
    assert options.tools == [] and options.allowed_tools == []
    assert not options.mcp_servers


def test_c2f_forces_delegation_prompt_and_subagents(tmp_path: Path):
    task = {"id": "qa-0", "question": "who?", "start": "ns:a",
            "answer": "ns:d", "hops": 2}
    prompt = render_prompt(task, "qa", "C2f", None)
    assert "symbolic-explorer" in prompt
    assert "run_in_background: false" in prompt
    # C2 (unforced) must NOT carry the mandate — that's the ablation.
    assert "symbolic-explorer" not in render_prompt(task, "qa", "C2", None)
    # Options: C2f gets subagents exactly like C2.
    kb = tmp_path / "kb.ttl"
    kb.write_text("@prefix ns: <http://nsai.local/ns#> .\n", encoding="utf-8")
    options = build_condition_options(kb, "C2f", None)
    assert options.agents and "Task" in options.allowed_tools


def test_b2_agentic_grep_condition(tmp_path: Path):
    task = {"id": "qa-0", "question": "who?", "start": "ns:a",
            "answer": "ns:d", "hops": 2}
    prompt = render_prompt(task, "qa", "B2", None)
    assert "kb.ttl" in prompt and "Grep" in prompt
    assert "@prefix" not in prompt          # facts are never inlined for B2
    kb = tmp_path / "kb.ttl"
    kb.write_text("@prefix ns: <http://nsai.local/ns#> .\n", encoding="utf-8")
    options = build_condition_options(kb, "B2", None)
    assert sorted(options.tools) == ["Glob", "Grep", "Read"]
    assert options.cwd == str(tmp_path)     # pinned to the scratch dir
    assert not options.mcp_servers          # no symbolic layer
    assert options.agents is None           # no subagents


def test_all_condition_options_raise_stream_buffer(tmp_path: Path):
    # A broad Grep over the 4.3MB MetaQA Turtle file produced a >1MiB tool
    # result and killed a B2 run at the SDK's default buffer; every
    # condition streams tool results, so all get the raised limit.
    from experiments.harness import MAX_BUFFER_SIZE

    kb = tmp_path / "kb.ttl"
    kb.write_text("@prefix ns: <http://nsai.local/ns#> .\n", encoding="utf-8")
    for cond in ("B0", "B2", "C1", "C2"):
        options = build_condition_options(kb, cond, None)
        assert options.max_buffer_size == MAX_BUFFER_SIZE, cond
