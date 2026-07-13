# Don't Run the Reasoner on Dirty Data: A Practical Recipe for Auditing Knowledge Bases with LLM Agents

*Short paper draft for the NeSy 2026 Industry Track (deadline 2026-07-17). Target length: 4–6 pages. Markdown source; LaTeX conversion pending.*

**Authors:** [TODO: author list and affiliations]

---

## Abstract

Industrial LLM agents increasingly maintain knowledge bases (KBs) that must be audited for internal contradictions before they are trusted downstream. A natural neuro-symbolic design gives the agent access to a classical reasoner — in our case OWL-RL forward-chaining closure over an RDF KB — and asks it to report constraint violations. We report a failure mode of this design that we believe is under-appreciated in practice: **running a sound reasoner over an already-contradictory KB silently amplifies the contradictions**. On our audit benchmark, functional-property violations interacting with `owl:sameAs` chains caused the closure to inflate 19 ground violations into 95 apparent ones (and 20 into 393 on a larger grid), driving the agent's report precision to 0.05 — with run-to-run predictions that were verbatim-identical, because the agent was faithfully transcribing deterministic-but-poisoned reasoner output. Attempting to repair reports with per-fact provenance lookups cost $15.51 for 318 tool calls on one grid and still capped recall at 0.60. Our fix is an engineering change, not a modeling one: replace open-ended closure access with a purpose-built `kb_violations` tool that enumerates violations **on asserted triples, before closure**, and returns each violation bundled with its provenance in a single call. After the fix, the same agent achieves precision and recall 1.0 on all four benchmark grids, for both single-agent and delegating configurations, across three runs each — at roughly $0.2 per run instead of $15. We distill the episode into a deployment recipe: audit before you infer, tag inferred triples, ship provenance with every verdict, and gate LLM output through deterministic checks. A companion multi-hop QA experiment (accuracy 0.79 → 0.97 when a symbolic answer-checker is made a mandatory gate rather than an optional tool) supports the same design thesis, and a negative control shows the determinism is what matters: substituting an LLM judge for the deterministic check leaves false completion declarations statistically unchanged (p = 0.09), because a judge from the same model family fails where the agent fails.

---

## 1. Introduction

- **Setting.** LLM agents that accumulate and act on structured knowledge are moving into production (agent memory engines, enterprise KG copilots). Before the KB is trusted — for retrieval, for planning, for compliance — someone has to answer: *is this KB internally consistent, and if not, exactly which assertions conflict?* This is the audit task.
- **The obvious NeSy design.** Give the agent a classical reasoner as a tool: compute the OWL-RL closure, query for violations of functional properties / disjointness, report them. Deterministic inference should be exactly what LLMs lack. This is the textbook neuro-symbolic division of labor.
- **The catch.** Classical reasoners are sound *under the assumption that the KB is consistent enough for their semantics to be meaningful*. An audit KB is, by construction, **not** consistent — that is the whole point of auditing it. Running closure first is therefore running the reasoner outside its contract, and it fails in the worst possible way for an LLM consumer: it produces large volumes of confident, internally coherent, deterministic garbage that the LLM has no basis to doubt.
- **Contributions.**
  1. A characterized industrial failure mode: closure-amplified pseudo-violations ("reasoner poisoning"), with a mechanism-level diagnosis (verbatim transcription of closure output; Section 3).
  2. An engineering fix — `kb_violations`: pre-closure enumeration over asserted triples with bundled provenance — that restores P/R to 1.0 on all grids while cutting cost ~75x (Section 4).
  3. A deployment recipe generalizing the fix: deterministic checks as mandatory gates on LLM output paths, validated a second time on multi-hop KGQA (0.79 → 0.97; Section 5).
  4. A negative control isolating *determinism* as the active property of a gate: an LLM loop-judge over completion declarations does not significantly reduce false completions on contradictory specs (p = 0.091), while model capability dominates (p = 2.9e-08) and composition-implied contradictions defeat even the strong model under both protocols (Section 6).

## 2. System and Task

- **System.** An LLM agent (Claude Sonnet-class model) with tool access to a persistent RDF/OWL KB: `kb_find`, `kb_sparql`, `kb_verify`, `kb_infer` (OWL-RL closure via `owlrl`/`rdflib`), `kb_provenance` (source lookup per triple). A delegating variant (C2) can spawn a symbolic-explorer subagent; C1 is single-agent. Baseline B1 inlines the entire KB in context.
- **Audit benchmark.** Synthetic KBs on a 2x2 grid — {500, 2000} assertions x {5, 20} planted contradictions (`a500c5` … `a2000c20`) — where every planted contradiction is a violation of a functional-property or disjointness constraint with known ground truth. Metric: precision/recall of the agent's reported violation set against the planted set. Three runs per cell.
- **Initial results (pre-fix).** B1 (full KB in context) achieved P=R=1.00 in all 12 runs at these scales ($0.7–2.2/run). The symbolic-tool conditions, counter-intuitively, were *bimodal*: C1 collapsed to P ≈ 0.05 in 7 of 12 runs, C2 in 4 of 12, while recall stayed near 1.0. The symbolic layer — the component added to make the audit trustworthy — was the source of the untrustworthy reports.

## 3. Error Analysis: How a Sound Reasoner Poisons the Audit

Full-population analysis of the failed runs (predictions checked mechanically against the KB, not by human judgment) established the mechanism:

- **Closure amplification.** On `a500c5`, the base KB contains 19 assertion-level violations. Under OWL-RL semantics, functional-property violations entail `owl:sameAs` between the conflicting objects; these sameAs facts chain (281 derived pairs), and substitution then manufactures 95 *apparent* violations that exist only in the closure. On the larger grid, 20 ground violations ballooned to 393. Precision 20/393 ≈ 0.05 is not the LLM "hallucinating" — every reported item is really present in the inferred graph.
- **Verbatim transcription.** The failed runs' prediction sets were **bit-identical across runs and across C1/C2**. The agent was doing exactly what NeSy doctrine says it should: deferring to the deterministic reasoner and copying its output. Determinism amplified the error instead of containing it. The runs that scored P=1.00 were precisely those that either never called `kb_infer` or filtered its output through `kb_provenance`.
- **Provenance-as-repair does not scale.** The one strategy that partially worked — checking each candidate violation's provenance one triple at a time — took 318 `kb_provenance` calls and $15.51 on `a500c20`, and still reached only R=0.60 before the agent gave up. Per-fact repair loops are the wrong shape for the job.
- **Takeaway for practitioners.** The failure is not in the LLM, not in the reasoner, and not in the KB. It is in the *composition*: an inference tool whose precondition (consistency) is violated by the very task it is used for, exposed to a consumer (the LLM) that cannot inspect that precondition.

## 4. The Fix: Enumerate Before Closure, Ship Provenance With the Verdict

We replaced open-ended reasoner access with a purpose-built audit tool (prompt/tooling revision 4):

- **`kb_violations`**: enumerates functional-property and disjointness violations **over asserted triples only, before any closure**, and returns each violation with its supporting triples and their provenance records in one call. The 318-call repair loop becomes a single tool call.
- **Asserted/inferred tagging**: results of `kb_find`/`kb_sparql` after a `kb_infer` call are tagged `asserted` vs `inferred`, so closure output can never masquerade as ground assertions. `kb_infer` additionally emits a cascade warning when the KB contains constraint violations, telling the agent (and the log reader) that closure over this KB is not audit-grade evidence.
- **Result.** Re-running the audit benchmark at rev4: **precision and recall 1.0 on all 4 grids x {C1, C2} x 3 runs** — 24/24 runs, no bimodality — at approximately **$0.2 per run versus $15** for the provenance-repair strategy. The complete loop — error analysis → localized design change → full recovery, verified against the identical benchmark — took one tool implementation and no model or prompt-strategy changes beyond documenting the new tool.
- **Why this is the right shape.** The fix moves the audit semantics *into* the deterministic layer (the tool decides what counts as a violation and proves it with provenance) and shrinks the LLM's job to orchestration and report formatting — the part it was already doing correctly (recall was never the problem).

## 5. The Same Principle, Second Domain: Gating Multi-hop QA

The audit fix instantiates a general principle: **deterministic checks help little as optional tools and help enormously as mandatory gates on the output path.** We validated this independently on MetaQA (133,582-triple KB, 600 questions, 1/2/3-hop, exact match):

- With symbolic KB tools *available but optional*, the agent scored **0.790** — below a pure agentic-grep baseline with no symbolic tools at all (**0.860**).
- Adding two gates — `kb_check_answer` (the proposed answer must exist in the KB, type-match the question, and not be a trivial attribute of the start entity) and a verify-before-FINAL protocol (the final-hop triple must pass `kb_verify` before the agent may answer) — raised accuracy to **0.970** (per-hop 0.950/0.980/0.980). McNemar vs. the grep baseline: 66/0 discordant pairs, p = 2.7e-20; vs. the optional-tools condition: 108/0, p = 6.2e-33.
- An ablation adding deterministic path traversal (`kb_path`) on top of the gates scored 0.962, not significantly different from gates alone (10/5 discordant, p = 0.3): **the gate is the ceiling; symbolizing the search itself adds nothing further.**
- Re-running only the 126 previously-failed tasks under the gated protocol recovered 106/126 (84%) with **zero regressions** on a 126-task matched control — the same "analyze → fix → verified recovery" loop as the audit story.

In both domains the winning move was identical: do not ask the LLM to *choose* to consult the symbolic layer; make the symbolic check a precondition for output.

## 6. Negative Control: An LLM Judge Is Not a Gate

An obvious objection to §5: perhaps *any* additional verification step helps, and the gates' benefit comes from "checking twice" rather than from the check being deterministic. We tested this directly on a third task family — completion declarations in code fixing — where the extra verifier is another LLM instead of a symbolic check.

- **Setup.** 50 small Python fix tasks (spec + implementation + hidden tests), of which 20 have deliberately contradictory specifications (infeasible), stratified into three subtlety tiers: *direct-example* (two requirements assign different outputs to the same explicit input), *rule-vs-example* (a worked example violates a stated rule), and *implied* (two individually-sound rules that collide only on specific inputs). The agent must end with `FINAL: DONE` or `FINAL: STALLED`; the correct behaviour on an infeasible spec is STALLED. Conditions: **self** (the agent judges its own completion) vs **judge** (a loop-judge subagent must approve every declaration). A *false completion* is declaring DONE while hidden tests fail. Machine validation guarantees infeasibility (every infeasible task's hidden cases contain at least one same-input/different-expected pair).
- **Result: the LLM judge does not significantly reduce false completions.** On a Haiku-class model, self falsely completes 19/20 infeasible tasks and judge 15/20 (one-sided Fisher p = 0.091, n.s.); on the direct-example tier the judge is *worse* than self (5/5 vs 4/5). On a Sonnet-class model self and judge are indistinguishable (2/20 vs 3/20, p = 0.83). What actually dominates is model capability: 19/20 vs 2/20 across models under the identical protocol (p = 2.9e-08).
- **The subtle tier cracks the strong model too.** On the implied tier, even the Sonnet-class model falsely completes 2/5 — in *both* conditions. The judge does not rescue it.
- **Reading.** The judge is sampled from the same model family and reads the same specification, so its errors are **correlated** with the agent's — a second draw from the same error distribution, not an orthogonal check. Where the judge did help (rule-vs-example on the weak model, 2/5 vs 5/5), the benefit came from a fresh reading of the whole spec, i.e. from diversity, not rigor — and diversity does not compound into a guarantee. This is precisely the structural weakness of LLM self-verification that deterministic gates avoid: a KB membership test or a solver verdict has an error model *independent* of the LLM's. Notably, our contradictory specs are small arithmetic contracts: the conjunction of their requirements is mechanically UNSAT for an SMT solver. A spec-to-SMT gate — declare STALLED iff the constraint set is unsatisfiable — is the constructive counterpart of this negative result, which we leave as future work.

## 7. Deployment Recipe

For teams pairing LLM agents with KBs and classical reasoners:

1. **Audit before you infer.** Never run closure/materialization as a step of contradiction detection. Enumerate violations on asserted triples; treat the reasoner's consistency precondition as an input contract to check, not an assumption.
2. **Tag inferred triples.** Any query surface that can return closure output must label it. An LLM cannot distinguish asserted from entailed facts on its own, and will transcribe whichever it is given.
3. **Ship provenance with the verdict, in one call.** Per-fact provenance repair loops are 1–2 orders of magnitude more expensive and still lossy. Design audit tools to return (violation, evidence, sources) as a unit.
4. **Gate, don't offer.** Deterministic validators on the output path (answer checkers, verify-before-final protocols) beat the same validators offered as optional tools — in our QA experiment the optional version *underperformed having no symbolic tools at all*, while the gated version dominated everything (0.79 vs 0.86 vs 0.97).
5. **Verify recoveries against a control.** Failed-task reruns need a matched non-failed control set to certify zero regression (ours: 106/126 recovered, 0/126 regressed).
6. **Log verbatim tool output.** The bit-identical prediction sets across runs were the smoking gun that localized the fault to the reasoner, not the LLM. Without raw tool-output logs the failure would have looked like stochastic hallucination.
7. **A gate must be deterministic — an LLM judge is not a gate.** A judge model drawn from the same family is a second sample from the same error distribution: in our completion-declaration experiment it left false completions statistically unchanged (p = 0.09), was worse than self-judgment on one tier, and cost 2–2.5x. Budget spent on judge calls is better spent on the model itself or on deterministic checks.

## 8. Related Work

*(Directional; full citations verified in our bibliography survey — see notes below.)*

- **LLM + formal solvers** (Logic-LM, LINC, SatLM, Proof of Thought): LLM formulates, deterministic solver reasons. These assume the symbolic substrate is trustworthy; our contribution is the failure case where the substrate's precondition is violated by the task, and the tool-contract redesign that follows.
- **Verify-before-output / self-refinement** (Chain-of-Verification, SelfCheckGPT, self-consistency): verification is performed *by the LLM itself*, inheriting its failure modes. Our gates differ in that the check is **deterministic and symbolic** — a KB membership/type/provenance test with a binary verdict — which is why it can be made a hard precondition rather than advice. §6 measures this difference directly: an LLM loop-judge left false completion declarations statistically unchanged where deterministic checks would decide mechanically. FormalJudge (Dafny+Z3 supervision of agents) is the closest formal-gate relative; KG-Guard (groundedness detection for KBQA answers) is a detector counterpart to our constructive check.
- **Agent memory engines** (Zep/Graphiti, A-MEM, WorldDB, ElephantBroker): persist knowledge but leave verification to the LLM or embeddings; the STALE and Supersede measurements that frontier LLMs miss invalidated/superseded memories motivate deterministic auditing. Ontology-constrained enterprise agents (arXiv:2604.00555) add output-side validation but no closure semantics or provenance; Salovskii (arXiv:2604.20795) is the nearest persistent RDF/OWL + constraint-validation loop, without pre-closure audit semantics or provenance-bundled verdicts.
- **Reasoning over inconsistent KBs**: paraconsistent and repair-based semantics study this formally; our point is the *systems* consequence when a standard OWL-RL materializer is composed with an LLM consumer in production. [TODO: pick 1–2 canonical citations for inconsistency-tolerant semantics before submission.]

## 9. Limitations

- **Synthetic audit KBs.** The grids (≤2000 assertions, planted functional/disjointness violations) are synthetic and small enough that a full-context LLM baseline (B1) also achieves P/R 1.0; the symbolic pipeline's advantage at these scales is cost and mechanism, not headline accuracy. Real enterprise KBs with organic contradictions remain future work.
- **RQ on claim verification not discriminative.** Our companion claim-verification experiment saturated — all non-trivial conditions scored 1.0 on synthetic claims at every scale — so it could not test whether symbolic grounding is *necessary* there; we report it as a null design, not as evidence. An agentic-search baseline on inference-required claims is planned but not yet run.
- **One model family, limited runs.** Audit results are 3 runs/cell on one Sonnet-class model (a cheaper-model replication is planned); QA gate results are single-run per condition with paired McNemar tests rather than cross-run variance.
- **Violation classes.** `kb_violations` currently covers functional-property and disjointness constraints; the pre-closure enumeration strategy should extend to other OWL-RL-expressible constraints, but we have not measured it.
- **Benchmark-shaped gates.** The start-entity-exclusion check in `kb_check_answer` compiles a MetaQA answer convention into the gate; we present it as an instance of the general pattern "compile question semantics into a symbolic check," and generality beyond MetaQA is untested.
- **Negative-control scope.** The completion-declaration experiment uses 20 hand-written infeasible specs (single run per cell), and the judge is drawn from the same model family as the agent; a cross-family judge, or best-of-N judging, might decorrelate errors and was not tested. The claimed conclusion is the modest one the data supports: this common judge configuration is not a substitute for a deterministic gate.

## 10. Conclusion

A sound reasoner composed with an inconsistent KB and an obedient LLM produced a 0.05-precision audit with perfectly reproducible wrong answers — and the fix was neither a better model nor a better prompt, but a better tool contract: enumerate violations before closure, attach provenance to every verdict, and gate LLM output through deterministic checks. The same gating principle, transplanted to multi-hop QA, turned a losing symbolic layer (0.79, below a grep-only baseline's 0.86) into a decisively winning one (0.97). And the negative control shows the determinism is essential: substituting an LLM judge for the deterministic check left false completion declarations statistically unchanged, because a judge from the same family fails where the agent fails. For industrial NeSy systems the lesson is architectural: **the symbolic layer earns its keep not as an optional instrument the LLM may consult, but as a mandatory, deterministic gate the LLM's output must pass through.**

---

## Appendix A. Headline numbers (all measured; source: `results/*.jsonl`, key `correct`)

| Experiment | Condition | Result |
|---|---|---|
| Audit (4 grids x 3 runs) | C1/C2 pre-fix | Recall ≈ 1.0; Precision bimodal, ≈0.05 in 7/12 (C1) and 4/12 (C2) runs |
| Audit mechanism | closure amplification | 19 → 95 apparent violations (a500c5, via 281 sameAs pairs); 20 → 393 (larger grid) |
| Audit repair-by-provenance | C1, a500c20 | 318 calls, $15.51, R = 0.60 |
| Audit post-fix (rev4) | C1 & C2, all grids, 3 runs | **P = R = 1.0 (24/24 runs)**, ≈ $0.2/run |
| MetaQA 600Q | C1 rev2 (tools optional) | 0.790 ($21.23) |
| MetaQA 600Q | B2 agentic-grep | 0.860 ($22.28) |
| MetaQA 600Q | C1 rev5 (gates: S1+S4) | **0.970** (0.950/0.980/0.980 per hop; $38.26) |
| MetaQA 600Q | C1 rev6 (rev5 + kb_path) | 0.962 (n.s. vs rev5, p = 0.3) |
| Significance | rev5 vs B2 / vs rev2 | 66/0, p = 2.7e-20; 108/0, p = 6.2e-33 (McNemar) |
| Failed-task rerun | rev5 protocol | 106/126 recovered (84%); 0/126 control regressions |
| Completion declarations (20 infeasible specs) | self vs judge, Haiku-class | false completions 19/20 vs 15/20 (Fisher p = 0.091, n.s.) |
| Completion declarations | self, Haiku vs Sonnet-class | 19/20 vs 2/20 (p = 2.9e-08) — capability dominates |
| Completion declarations | implied tier, Sonnet-class | 2/5 false completions in **both** self and judge |
