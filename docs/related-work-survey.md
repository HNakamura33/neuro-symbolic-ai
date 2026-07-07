# 先行研究調査 — 論文 Related Work / References の書誌検証記録

実施日: 2026-07-07 / 対象: [paper-draft.md](paper-draft.md) の引用文献

## 調査方法

deep-research ワークフロー2回 + ピンポイント検証エージェント3体で実施。全書誌は
**一次情報源(公式プロシーディングス、ACL Anthology、arXiv abstract ページ、OpenReview、
DBLP、Crossref)を実際に取得して検証**した。deep-research 分は各クレームに対して
3票の敵対的検証(反証を試みる独立エージェント3体)を行い、採用したものはすべて
3-0 全会一致。記憶ベースの書誌(実在しない引用のリスク)は一切採用していない。

- 第1弾(deep-research): 分野1–3, 6 → 9本確定
- 第2弾(deep-research): 分野4, 5, 7の一部 → 10本確定
- 第3弾(検証エージェント3体): ベンチマーク残り+NeSy 概観+近接研究 → 19本確定

## 検証済み文献一覧(38件)

### 1. ニューロシンボリック AI 概観

| 文献 | Venue | 備考 |
|---|---|---|
| Kautz, "The third AI summer: AAAI Robert S. Engelmore Memorial Lecture" | AI Magazine 43(1):105–125, 2022. DOI 10.1002/aaai.12036 | **講演(2020)ではなく論文版を引くこと**。arXiv 版は存在しない。正 DOI は Wiley(AAAI OJS の代替 DOI 10.1609/aimag.v43i1.19122 は使わない) |
| Garcez & Lamb, "Neurosymbolic AI: the 3rd wave" | Artificial Intelligence Review 56(11):12387–12406, 2023. arXiv:2012.05876. DOI 10.1007/s10462-023-10448-w | 誌上タイトルは小文字 "the 3rd wave"。arXiv 側に journal-ref 未記載だが Crossref で掲載確認済み |
| Sarker, Zhou, Eberhart, Hitzler, "Neuro-symbolic artificial intelligence" | AI Communications 34(3):197–209, 2021. arXiv:2105.05330. DOI 10.3233/AIC-210084 | **誌上正式タイトルは短い方**(arXiv 版 "…: Current Trends" とは異なる)。引用年は 2021 が標準 |
| Hitzler, Eberhart, Ebrahimi, Sarker, Zhou, "Neuro-symbolic approaches in artificial intelligence" | National Science Review 9(6):nwac035, 2022. DOI 10.1093/nsr/nwac035 | 予備(現在の draft では未引用) |

### 2. Tool-augmented LLM

| 文献 | Venue | 備考 |
|---|---|---|
| Schick et al., "Toolformer: Language Models Can Teach Themselves to Use Tools" | NeurIPS 2023 (Oral). arXiv:2302.04761 | **著者数が版で異なる**: arXiv 8名 / NeurIPS camera-ready 9名(Eric Hambro の有無)。camera-ready の9名を使う |
| Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models" | ICLR 2023 (notable-top-5%). arXiv:2210.03629 | 公式 BibTeX キー yao2023react。Narasimhan は OpenReview 表記 "Karthik R Narasimhan" |

### 3. プログラム補助推論

| 文献 | Venue | 備考 |
|---|---|---|
| Gao et al., "PAL: Program-aided Language Models" | ICML 2023, PMLR 202:10764–10799. arXiv:2211.10435 | 「分解だけが LLM の仕事、求解はインタプリタ」の分業を明示した原典 |
| Chen et al., "Program of Thoughts Prompting: Disentangling Computation from Reasoning for Numerical Reasoning Tasks" | TMLR 2023. arXiv:2211.12588 | PAL と同時期(2022-11)の独立提案。併記して引用 |

### 4. LLM + 形式論理ソルバー(本論文の直接の先行研究)

| 文献 | Venue | 備考 |
|---|---|---|
| Pan et al., "Logic-LM: Empowering Large Language Models with Symbolic Solvers for Faithful Logical Reasoning" | Findings of EMNLP 2023, pp. 3806–3824. arXiv:2305.12295 | 「LLM が記号的定式化へ翻訳、決定論的ソルバーが推論」を abstract で明言。ソルバーエラーによる self-refinement も持つ |
| Olausson et al., "LINC: A Neurosymbolic Approach for Logical Reasoning by Combining Language Models with First-Order Logic Provers" | EMNLP 2023 main, pp. 5153–5176. arXiv:2310.15164 | **Outstanding Paper Award**。LLM は FOL への意味解析器のみ、演繹は Prover9 |
| Ye et al., "SatLM: Satisfiability-Aided Language Models Using Declarative Prompting" | NeurIPS 2023. arXiv:2305.09656 | 宣言的仕様を LLM が生成し Z3 が求解。「パースされた仕様に対する解の正しさ」保証は本論文の健全性論証と同型 |
| Ganguly et al., "Proof of Thought: Neurosymbolic Program Synthesis allows Robust and Interpretable Reasoning" | NeurIPS 2024 System-2 Reasoning At Scale Workshop. arXiv:2409.17270 | JSON DSL → FOL → Z3 検証。単発 QA 向けでエージェントではない |

### 5. 知識グラフ + LLM / GraphRAG / KBQA

| 文献 | Venue | 備考 |
|---|---|---|
| Pan et al., "Unifying Large Language Models and Knowledge Graphs: A Roadmap" | IEEE TKDE 36(7):3580–3599, 2024. arXiv:2306.08302. DOI 10.1109/TKDE.2024.3352100 | 3枠組み taxonomy。TKDE 版は arXiv 版の condensed 版(頁参照時は注意) |
| Edge et al., "From Local to Global: A Graph RAG Approach to Query-Focused Summarization" | **arXiv:2404.16130(プレプリント、査読 venue なし)** | 2026-07-07 時点で journal-ref なし。**投稿直前に再確認**。KG 自体が LLM 生成物である点が本論文との対比軸 |
| Saxena, Tripathi, Talukdar, "Improving Multi-hop Question Answering over Knowledge Graphs using Knowledge Base Embeddings" | ACL 2020, pp. 4498–4507. DOI 10.18653/v1/2020.acl-main.412 | EmbedKGQA。MetaQA 上の代表手法(実験2の比較文脈) |
| Sun et al., "Think-on-Graph: Deep and Responsible Reasoning of Large Language Model on Knowledge Graph" | ICLR 2024. arXiv:2307.07697 | LLM⊗KG パラダイム: LLM エージェントが KG 上でビームサーチ。**推論主体が LLM のまま**という点が本論文との対比軸 |

### 6. Self-verification / ハルシネーション検出

| 文献 | Venue | 備考 |
|---|---|---|
| Wang et al., "Self-Consistency Improves Chain of Thought Reasoning in Language Models" | ICLR 2023. arXiv:2203.11171 | |
| Manakul, Liusie, Gales, "SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models" | **EMNLP 2023 main**(Findings ではない), pp. 9004–9017. arXiv:2303.08896 | BibTeX キー manakul-etal-2023-selfcheckgpt |
| Dhuliawala et al., "Chain-of-Verification Reduces Hallucination in Large Language Models" | **Findings of ACL 2024**(arXiv-only ではない), pp. 3563–3578. arXiv:2309.11495 | 4段階検証はすべて LLM 内部 = 循環性の代表例。著者表記は Anthology の "Jason Weston"(middle initial なし)に従う |

### 7. LLM + SMT / 形式検証(ソフトウェア)

| 文献 | Venue | 備考 |
|---|---|---|
| Chakraborty et al., "Ranking LLM-Generated Loop Invariants for Program Verification" | Findings of EMNLP 2023. arXiv:2310.09342 | **arXiv と Anthology で著者順が一部異なる**(Lal/Musuvathi)。Anthology 順に従う |
| Ma et al., "SpecGen: Automated Generation of Formal Program Specifications via Large Language Models" | ICSE 2025. arXiv:2401.08807. DOI 10.1109/ICSE55347.2025.00129 | arXiv に journal-ref 未記載だが ACM DL / IEEE / 公式プログラムで確認済み |
| Bharti et al., "Loop Invariant Generation: A Hybrid Framework of Reasoning optimised LLMs and SMT Solvers" | arXiv:2508.00419 **(プレプリント、under review)** | Code2Inv 133/133。venue 未確定 — 投稿前に再確認 |

### 8. ベンチマーク原典

| ベンチマーク | 文献 | Venue | 備考 |
|---|---|---|---|
| ProofWriter | Tafjord, Dalvi, Clark | **Findings of ACL-IJCNLP 2021**(joint volume), pp. 3621–3634. DOI 10.18653/v1/2021.findings-acl.317 | |
| PrOntoQA | Saparov & He, "Language Models Are Greedy Reasoners: A Systematic Formal Analysis of Chain-of-Thought" | ICLR 2023. arXiv:2210.01240 | **正式表記は "PrOntoQA"**(ProntoQA は不正確)。独立したデータセット論文はなく、この論文を引く |
| FEVER | Thorne, Vlachos, Christodoulopoulos, Mittal | NAACL-HLT 2018, pp. 809–819. DOI 10.18653/v1/N18-1074 | |
| MetaQA | Zhang, Dai, Kozareva, Smola, Song, "Variational Reasoning for Question Answering with Knowledge Graph" | AAAI 2018. arXiv:1709.04071. DOI 10.1609/aaai.v32i1.12057 | **"MetaQA" の名称は論文本文に現れない**(公式リポジトリ github.com/yuyuz/MetaQA 由来)。本文言及時は「Zhang et al. (2018) が導入したデータセット」と書く |
| CLUTRR | Sinha, Sodhani, Dong, Pineau, Hamilton | EMNLP-IJCNLP 2019, pp. 4506–4515. arXiv:1908.06177. DOI 10.18653/v1/D19-1458 | |
| 2WikiMultiHopQA | Ho, Duong Nguyen, Sugawara, Aizawa, "Constructing A Multi-hop QA Dataset for Comprehensive Evaluation of Reasoning Steps" | COLING 2020, pp. 6609–6625. arXiv:2011.01060 | |
| QuixBugs | Lin, Koppel, Chen, Solar-Lezama | SPLASH Companion 2017, pp. 55–56. DOI 10.1145/3135932.3135941 | arXiv 版なし |
| HumanEval | Chen et al., "Evaluating Large Language Models Trained on Code" | **arXiv:2107.03374(プレプリントのみ、査読 venue なし)** | 著者58名。会議・論文誌版は存在しない |
| MBPP | Austin et al., "Program Synthesis with Large Language Models" | **arXiv:2108.07732(プレプリントのみ)** | |
| EvalPlus (HumanEval+/MBPP+) | Liu, Xia, Wang, Zhang, "Is Your Code Generated by ChatGPT Really Correct? …" | NeurIPS 2023. arXiv:2305.01210 | |
| LiveCodeBench | Jain et al. | ICLR 2025. arXiv:2403.07974 | |
| SWE-bench | Jimenez et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" | ICLR 2024. arXiv:2310.06770 | |
| SWE-bench Verified | Chowdhury et al., "Introducing SWE-bench Verified" | OpenAI, 2024-08(Web 資料) | **部分検証**: 原典ページが 403 のため著者リストは間接ソース(同ページを引用する複数論文)経由。**投稿前に原典の目視確認が必須** |

### 9. 2024–2026 近接研究と差分分析(最重要)

**最近接の2本(必ず差別化すること):**

- **MCP-Solver** — Szeider, "MCP-Solver: Integrating Language Models with Constraint Programming Systems". arXiv:2501.00539(プレプリント)。
  MCP 経由で LLM を MiniZinc(CP)・PySAT(SAT)・Z3(SMT)に接続。
  **共通**: 「LLM が定式化、ソルバーが求解」+複数ソルバー統合。
  **差分**: ソルバーへのツールアクセス層に留まり、永続 RDF/OWL KB・出典管理・サブエージェント委譲・コーディング検証パイプラインを持たない。
- **SymAgent** — Liu, Zhang, Lin, Yang, Peng, Yin. WWW 2025. arXiv:2502.03283. DOI 10.1145/3696410.3714768。
  KG を動的環境とみなし、LLM エージェント(Agent-Planner/Agent-Executor)が多段推論。オンライン探索+オフライン方策更新の自己学習。
  **差分**: 推論の主体が LLM のまま(OWL-RL 等の決定論的推論器なし)。CSP/SMT なし、出典管理なし、永続 KB 設計なし、コーディング検証なし。逆に本論文にない自己学習(fine-tuning)を持つ。

**永続 KG メモリ系(「保存はするが推論・検証が LLM 依存」):**

- **Zep** — Rasmussen, Paliychuk, Beauvais, Ryan, Chalef, "Zep: A Temporal Knowledge Graph Architecture for Agent Memory". arXiv:2501.13956(プレプリント)。時間認識 KG エンジン Graphiti。KG は独自形式(RDF/OWL 非標準)、決定論的推論なし、検索は LLM/埋め込み依存。
- **A-MEM** — Xu, Liang, Mei, Gao, Tan, Zhang, "A-MEM: Agentic Memory for LLM Agents". NeurIPS 2025. arXiv:2502.12110。Zettelkasten 型メモリ。記憶の構造化・リンク・進化を LLM 自身が行うため保存内容の正しさに決定論的保証がない。

**Related Work 構成への示唆**(paper-draft の「近接する同時代研究」段落に反映済み):
メモリ系は「保存はするが推論・検証が LLM 依存」、ソルバー・検証系(MCP-Solver, Proof of Thought, ループ不変条件系)は「検証はするが単発タスクで永続知識がない」。本論文は両者(永続 KB + 決定論的判定 + 複数ソルバー + 委譲 + コーディング検証)を単一エージェントに統合する位置づけ。

**書誌未検証のため不採用とした候補**(必要なら追加検証): AgenticDomiKnowS (arXiv:2601.00743 — 検証済みだが NeSy 学習プログラム開発支援で主題がずれるため draft では未引用)、Ontology-Constrained Neural Reasoning (arXiv:2604.00555)、VERGE (arXiv:2601.20055)。

## 投稿前チェックリスト

- [ ] SWE-bench Verified: OpenAI 原典ページの著者欄を目視確認(調査時 HTTP 403)
- [ ] GraphRAG (Edge et al.): 査読 venue に採択されていないか arXiv ページを再確認
- [ ] Bharti et al. (arXiv:2508.00419): under review → venue 確定していないか再確認
- [ ] Toolformer: BibTeX を camera-ready 9名で作成(arXiv 版 8名と混同しない)
- [ ] Chakraborty et al.: 著者順は ACL Anthology 版に従う
- [ ] 表記統一: PrOntoQA(大文字小文字)、"MetaQA" の名称出自の書き方
