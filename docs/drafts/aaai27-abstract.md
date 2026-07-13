# AAAI-27 Submission Package: Abstract + Paper Skeleton

*Abstract deadline 2026-07-21 (full paper later). This file: title candidates, 250-word abstract, contributions, and experimental skeleton.*

---

## 1. Title candidates

1. **Symbolic Gates, Not Symbolic Tools: Making Neuro-Symbolic Layers Pay Off in LLM Agents**
2. **The Gate Is the Point: Deterministic Output Verification Turns Losing Symbolic Layers into Winning Ones**
3. **From Optional Instrument to Mandatory Gate: Where Knowledge-Base Grounding Actually Helps LLM Agents**

Recommendation: (1) as primary — states the thesis as a design contrast, searchable keywords, no over-claim. (2) as fallback if a punchier title is wanted; (3) is the most conservative.

## 2. Abstract (250 words)

> Neuro-symbolic architectures pair LLM agents with knowledge bases and deterministic reasoners, on the premise that symbolic grounding improves reliability. We show that *how* the symbolic layer is attached matters more than whether it is present. On MetaQA multi-hop question answering (133K-triple KB, 600 questions), an agent given symbolic KB tools as optional instruments scores 0.790 exact match — *below* a purely neural agentic-search baseline with no symbolic tools at all (0.860). Re-attaching the identical symbolic capability as a mandatory gate on the output path — a deterministic answer validator plus a verify-before-final protocol, both of which must pass before the agent may answer — raises accuracy to 0.970 (McNemar vs. the search baseline: 66/0 discordant pairs, p=2.7e-20). An ablation that additionally replaces neural path-finding with deterministic graph traversal yields no further gain (0.962, p=0.3): the gate, not the symbolization of search, is the active ingredient. Error analysis explains why: 93% of 3-hop failures are question-misreading errors that a KB membership-and-type check detects mechanically, while the protective verify tool, when optional, is used least exactly where it is needed most (49% usage at 1-hop vs. 12% at 3-hop). A second domain, KB contradiction auditing, independently confirms the principle: an OWL-RL reasoner consulted freely amplified 19 ground violations into 95 spurious ones (precision 0.05), while a pre-closure violation enumerator with bundled provenance restored precision and recall to 1.0 across all test grids at 1/75th the cost. Symbolic layers earn their keep as gates, not tools.

*(Word count: ~248. Verify against the venue's exact limit before submission.)*

## 3. Contributions

1. **A design thesis with paired evidence:** the same symbolic capability loses to a purely neural agentic baseline when offered as an optional tool (0.790 < 0.860) and beats it decisively when enforced as a deterministic gate on the output path (0.970; 66/0 discordant, p=2.7e-20). To our knowledge this is the first controlled demonstration that attachment mode — tool vs. gate — flips the sign of a symbolic layer's contribution in an LLM agent.
2. **An ablation isolating the active ingredient:** adding deterministic path traversal (`kb_path`) on top of the gates gives no significant further gain (0.962 vs 0.970, 10/5 discordant, p=0.3). The benefit comes from gating outputs, not from symbolizing the search process.
3. **A mechanism-level error analysis** grounding the design: (a) 93% of 3-hop failures are start-entity attribute misreadings, mechanically detectable by a KB check; (b) optional `kb_verify` is protective (+9–12 pt when used) but its usage rate inverts with hop count (49% → 12%), motivating enforcement over advice; (c) the apparent "delegation hurts QA" result is a survivorship/plumbing artifact (structured-handoff analysis), illustrating how agent-level conclusions can be confounded by harness effects.
4. **A second-domain replication in KB auditing:** free access to an OWL-RL closure reasoner over contradictory KBs is actively harmful (sameAs-chain amplification, 19→95 / 20→393 pseudo-violations, precision 0.05, with bit-identical wrong predictions across runs), while a gated, pre-closure `kb_violations` enumerator with provenance achieves P=R=1.0 on all 4 grids x 2 agent configurations x 3 runs, cutting cost from ~$15 to ~$0.2 per run.
5. **A recovery protocol with non-regression control:** re-running only previously-failed tasks under the gated protocol recovers 106/126 (84%) with zero regressions on a 126-task matched control.

## 4. Paper skeleton

### 4.1 Introduction
- Framing: the field debates *whether* symbolic layers help LLM agents; we show the answer depends on the coupling. Optional tool → the agent under-uses it precisely under load and loses to pure neural search. Mandatory gate → large, statistically decisive win.
- Thesis sentence: *A symbolic layer attached as an optional tool loses to a pure-LLM agentic system; the same layer attached as a mandatory gate on the output path wins by a wide margin.*

### 4.2 System
- LLM agent + persistent RDF/OWL KB; tool suite (`kb_find`, `kb_sparql`, `kb_verify`, `kb_infer`); the two gates: `kb_check_answer` (existence, type-consistency with the question, start-entity exclusion — presented as "compiling question semantics into a symbolic check") and verify-before-FINAL protocol; `kb_violations` for auditing (pre-closure enumeration + provenance bundling); asserted/inferred tagging.

### 4.3 Experiment 1 — Multi-hop KGQA (main result)
- MetaQA, 133,582 triples, 600 questions (200 per hop), exact match, key `correct`.
- Conditions: B0 (no KB), B2 (agentic-grep, no symbolic tools), C1-rev2 (symbolic tools optional), C1-rev5 (tools + gates), C1-rev6 (rev5 + `kb_path` ablation).
- Results table: 0.193 (B0) / 0.860 (B2, per-hop .935/.810/.835) / 0.790 (rev2, .905/.685/.780) / **0.970 (rev5, .950/.980/.980)** / 0.962 (rev6, .950/.990/.945). McNemar: rev5 vs B2 66/0 p=2.7e-20; rev5 vs rev2 108/0 p=6.2e-33; rev5 vs rev6 10/5 p=0.3.
- Contamination probe as supporting evidence: entity-ID perturbation collapses B0 to 0.000 while symbolic conditions are nearly unchanged — the KB is genuinely consulted, not recalled.
- Failed-task recovery: 106/126 (84%), 0/126 control regressions.

### 4.4 Experiment 2 — Contradiction auditing (second domain)
- 2x2 grid ({500,2000} assertions x {5,20} contradictions), 3 runs, C1/C2.
- Pre-fix: recall ≈1.0 but bimodal precision, collapsing to 0.05; mechanism: OWL-RL closure over an inconsistent KB manufactures sameAs-chain pseudo-violations, which the agent transcribes verbatim (bit-identical predictions across runs). Per-fact provenance repair: 318 calls, $15.51, R=0.60.
- Post-fix (`kb_violations`): P=R=1.0 in all 24 runs, ≈$0.2/run.
- Reading: the audit result is the gate thesis in the contrapositive — an *ungated* symbolic reasoner can be worse than none.

### 4.5 Error analysis
- 3-hop failures: ~93% start-entity attribute misreadings (mechanically verified against KB); shared with the neural baseline (same misreading in 12/17 all-condition failures) → error is in question interpretation, detectable symbolically.
- Optional-verify usage inversion (49%/…/12% by hop) with +9–12 pt conditional benefit.
- 2-hop perturbation gain (+0.2) traced to surface-name satisficing (returned-pivot / echoed-start), not entity collision (6/55).
- Delegation caveat: "forced delegation hurts" was driven by a handoff bug (8.5% null answers); with nulls excluded delegation is competitive — reported as a cautionary methodological note. [TODO: include only if the H1 structured-handoff rerun is finished by full-paper time; abstract does not depend on it.]

### 4.6 Related work
- **LLM + formal solvers** (Logic-LM, LINC, SatLM, Proof of Thought, MCP-Solver): LLM formulates, solver decides — but per-episode, with the solver as a callable; none study tool-vs-gate attachment or the inconsistent-KB failure case.
- **Self-verification / self-refine** (Chain-of-Verification, SelfCheckGPT, self-consistency): the verifier is the LLM itself, so verification inherits the generator's failure modes and cannot be a hard precondition. Our differentiator: the check is **deterministic and symbolic** (KB membership, type position, provenance), yielding a binary verdict that can be enforced, not advised. FormalJudge (Dafny/Z3 supervision) is the nearest formal-gate relative; KG-Guard (post-hoc groundedness detection for KBQA) is a detector where ours is constructive and in-line.
- **KG-agent reasoning** (Think-on-Graph, SymAgent, EmbedKGQA lineage): reasoning agent remains the LLM; symbolic structure guides search rather than gating output.
- **Agent memory with formal claims** (Kumiho, Zep, WorldDB, TOKI; STALE/Supersede/DeltaLogic measurements): persistence and revision are formalized, but answer-time verification stays neural — complementary motivation.
- Positioning sentence: prior NeSy work asks *what* the symbolic layer computes; we ask *where in the agent loop it binds*, and show that binding site dominates.

### 4.7 Limitations (honest)
- **Claim-verification RQ saturated:** on synthetic claims, every non-trivial condition (including plain KB-in-context) scored 1.000 at all scales, so that experiment could not discriminate and cannot support the thesis; we report it as a null and do not use it as evidence. An agentic-search baseline on inference-required claims is future work.
- **Benchmark structure favors the neural baseline and shapes the gate:** MetaQA has verbatim entity mentions and line-oriented KB text (grep-friendly), and one gate component (start-entity exclusion) compiles a MetaQA answer convention; generality of "compile question semantics into checks" beyond MetaQA is untested.
- **Single model family; limited seeds:** headline QA numbers are single runs per condition on one Sonnet-class model with paired McNemar tests; audit results are 3 runs/cell. Cross-model (cheaper-model) replication is planned.
- **Synthetic audit KBs** (≤2000 assertions, planted violations); a full-context baseline also achieves P/R 1.0 there, so the audit contribution is mechanism and cost, not headline accuracy.
- Gates raise cost (~$38 vs ~$21–22 per 600 questions): the accuracy/cost trade-off should be stated plainly.

### 4.8 Conclusion
- Restate thesis; recipe (gate outputs, enumerate before closure, provenance with every verdict); future work: gated cascades (union headroom to 0.915 3-hop), cheap-model + gate configurations, self-evolution double-gate connection.

## 5. Submission notes

- All numbers above are measured (source of truth: `results/*.jsonl`, correctness key `correct`; per-hop rev5/rev6 figures recomputed 2026-07-13). No projected numbers appear in the abstract.
- Bibliography: use only entries verified in `docs/related-work-survey.md`; run its pre-submission checklist (Toolformer 9-author camera-ready, GraphRAG venue recheck, SWE-bench Verified authorship, etc.).
- MetaQA naming: cite as "the dataset introduced by Zhang et al. (2018)" on first mention (name not in original paper).
- Overlap management: the NeSy Industry short paper leads with the audit story; this paper leads with the QA gate thesis and uses audit as second-domain evidence. Keep abstracts non-identical and cross-cite if both are accepted.
