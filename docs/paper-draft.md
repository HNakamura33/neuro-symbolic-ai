# 記号層に接地された LLM エージェント: 知識グラフ・制約ソルバー・SMT による検証可能なニューロシンボリック分業

**ドラフト v0.4** — 本実行の実験結果は未記入(TBD)、パイロット結果(§4.0)のみ記入済み。実験設計は [experiment-plan.md](experiment-plan.md)、書誌検証の記録は [related-work-survey.md](related-work-survey.md) を参照。引用文献は全件一次情報源で書誌検証済み(2026-07-07)。
投稿先候補: NeSy / AAAI・IJCAI(ニューロシンボリック枠)/ ACL・EMNLP Findings(tool-augmented LLM 枠)。

---

## Abstract

大規模言語モデル(LLM)は自然言語の理解と柔軟な問題定式化に優れる一方、事実の想起・多段推論・自己検証を同一の確率的機構で行うため、誤りを「検証済み」として断定するハルシネーションを構造的に排除できない。本論文では、この問題に対して**役割の分業**による解を提案する: LLM は自然言語の理解と記号表現への定式化のみを担い、事実の保存・推論・検証・求解はすべて決定論的な記号層 — RDF 知識グラフ + OWL-RL 推論、有限領域制約ソルバー(CSP)、SMT ソルバー(Z3) — が行う。記号層が確認するまでエージェントは事実を断定しない。この設計を実装したエージェント `nsai` は、さらに(i)ツールコールの嵩む知識グラフ探索・矛盾監査を安価なモデルの専門サブエージェントに委譲する機構、(ii)静的解析による決定論的なコード知識グラフ構築(code2kb)、(iii)通常のコーディングに SMT による境界条件検証を組み込むハイブリッドコーディングモードを備える。

合成 KB による制御実験と公開ベンチマーク(ProofWriter, FEVER, MetaQA, CLUTRR, 2WikiMultiHopQA, QuixBugs, EvalPlus, SWE-bench Verified サブセット)での評価により、(1)3値主張判定における偽検証率が LLM 単体および long-context ベースラインに対して \_\_\_ 低下すること、(2)KB 規模の増大に対して long-context ベースラインの精度が劣化する一方、記号層接地は劣化しないこと(\_\_\_)、(3)残存する誤りが LLM の定式化層に局在すること(\_\_\_)、(4)SMT 組み込みコーディングが境界バグの検出・修正率と生成テストの変異検出力を改善すること(\_\_\_)を示す。

---

## 1. Introduction

LLM ベースのエージェントは、質問応答・知識管理・コーディングにおいて広く実用化されつつある。しかしその中核をなす LLM は、**事実の記憶・推論・検証を単一の確率的な次トークン予測で行う**という設計上の制約を持つ。この結果、次の3つの失敗様式が系統的に現れる:

1. **偽検証(false verification)** — 誤った、あるいは根拠のない主張を「確認済みの事実」として断定する。検証を求められた場合でも、LLM は自身のパラメトリック知識と文脈からの尤度で「もっともらしさ」を判定するに過ぎず、判定自体がハルシネーションでありうる。
2. **多段推論の劣化** — 中間エンティティを要するマルチホップ推論は、ホップ数と候補事実数の増大に対して急速に劣化する。文脈に全事実を与える long-context 構成でも、注意機構による「文脈からの検索」は決定論的なグラフ照合の代替にならない。
3. **自己検証の循環性** — 「自分の出力を自分で確認する」self-verification 系の手法 (Wang et al., 2023; Manakul et al., 2023; Dhuliawala et al., 2024) は、検証器が被検証器と同じ機構・同じバイアスを共有するため、系統誤差を検出できない。

これらに共通する根本原因は、**生成と検証が同じ機構に同居している**ことである。本論文はこの観察に基づき、徹底した分業アーキテクチャを提案する:

> **LLM は自然言語の理解と記号表現への定式化のみを担い、事実の保存・推論・検証・求解はすべて決定論的な記号層が行う。記号層が確認するまで、エージェントは事実を断定しない。**

この原則を実装したエージェント `nsai` において、記号層は3つの決定論的コンポーネントからなる: (a)RDF 知識グラフ + OWL-RL 推論(事実の保存と含意・矛盾判定)、(b)有限領域制約ソルバー(割当・スケジューリング等の厳密解列挙)、(c)SMT ソルバー Z3(数値・論理主張の証明と反例生成)。LLM から見ればこれらは9種のツールであり、主張の検証は `kb_verify` が **entailed / contradicted / unknown** の3値を根拠付きで返す。判定は OWL-RL 閉包上の照合であって尤度ではないため、同じ KB に対して常に同じ答えを返し、「知らないことを知らない」と言える。

分業には副次的な利点がある。第一に、**探索・監査の委譲**である。知識グラフのマルチホップ探索や全域的な矛盾走査はツールコールが嵩み、メインエージェントの文脈を圧迫する。これらを読み取り専用ツールのみを持つ専門サブエージェント(symbolic-explorer / kb-auditor)に委譲し、蒸留された結論だけを文脈に戻す。サブエージェントの作業は決定論的なツールの上を歩くだけなので、**安価な小型モデルで十分**という仮説が立つ(RQ5)。第二に、**構造の決定論的抽出**である。コードの import・呼び出し・継承といった構造的事実は `ast` による静的解析で LLM を介さず抽出でき(code2kb)、LLM は契約や意図といった意味層の抽出に専念できる。第三に、**コーディングへの拡張**である。ファイル操作等のコーディング作業はニューラル層が行い、境界条件・入力検証・ループ終了条件といった「危ういロジック」の検証のみを SMT に委ねるハイブリッドコーディングモードにより、分業原則は事実 QA を超えてソフトウェア開発にも適用できる。

本論文の貢献は以下である:

- **アーキテクチャ**: 事実の保存・推論・検証・求解を決定論的記号層に完全に委ね、LLM を定式化器として用いるエージェント設計。専門サブエージェントへの委譲、静的解析による構造層、SMT 組み込みコーディングを含む(§2)。
- **評価**: 難易度を独立制御できる合成 KB と、KB・ルールが明示的に付属する公開ベンチマークの両輪で、4つの研究課題(ハルシネーション抑制、スケーリング、矛盾検出、コーディング)を検証する実験設計と結果(§3–4)。学習データ汚染への3重の対策(B0 プローブ、条件間差分、エンティティ置換摂動)を含む。
- **誤りの局在の実証**: 記号層導入後に残る誤りが LLM の定式化層に局在することを層別誤り分析で定量化し(§5)、ニューロシンボリック分業の直接の証拠とする。

### 1.1 Scope

本論文は汎用コーディングエージェントとしての総合的優位を主張しない。主張の守備範囲は「明示的な事実・制約・境界条件が関与するタスクにおいて、記号層への接地が検証可能性と精度を改善する」ことである。リポジトリ探索や API 理解が律速となるタスク(フル SWE-bench)、シェル操作・環境構築が主体のタスク(Terminal-Bench 等)は測定対象と直交するため対象外とし、代わりに主張の範囲内である境界バグサブセット(実験4d、§3.7)で実リポジトリへの外的妥当性を確認する。どこで効き、どこでは効かないかを明示することが、本論文の誠実な主張形式である。

---

## 2. System

`nsai` は Claude Agent SDK 上に構築されたエージェント CLI である。図1に全体アーキテクチャを示す(本ドラフトでは README の Mermaid 図を転載予定 — TBD)。

### 2.1 設計原則

システム全体は単一の原則から導かれる: **ニューラル層(LLM)は理解と定式化、記号層は事実と判定。** LLM が生成するのは(i)トリプル、(ii)SPARQL クエリ、(iii)CSP の変数・領域・制約、(iv)SMT の式、のいずれかの記号表現であり、それらの評価結果 — 含意・矛盾・解・証明・反例 — は決定論的に計算される。エージェントのシステムプロンプトは「記号層が確認するまで事実を断定しない」ことを行動規範として明示する。

### 2.2 記号層

**知識グラフ(kb.py)** — KB は RDF トリプルの有限集合 $`G = \{(s, p, o)\}`$ として表現され、Turtle ファイルに永続化される。全追加は出典写像 $`\sigma`$(トリプル → 情報源)として provenance ログ(JSONL)に記録される。推論は OWL-RL ルール集合 $`\mathcal{R}`$ による演繹閉包で行う:

```math
\mathrm{cl}(G) \;=\; \bigcap \{\, H \mid H \supseteq G,\ H \text{ is closed under } \mathcal{R} \,\}
```
すなわち $`G`$ を含み $`\mathcal{R}`$ について閉じた最小のグラフ(最小不動点)である。$`\mathrm{cl}`$ は単調($`G \subseteq \mathrm{cl}(G)`$)かつ決定論的で、同じ $`G`$ に対して常に同じ閉包を返す(owlrl による実装)。扱う語彙は `rdfs:subClassOf` 連鎖、`owl:FunctionalProperty`、`owl:sameAs` / `owl:differentFrom` 等の OWL-RL プロファイルである。主張検証 `kb_verify` は、主張トリプル $`t = (s, p, o)`$ に対する全域的・決定論的な判定関数 $`V(G, t)`$ であり、次のように定義される:

```math
V(G, t) = \begin{cases} \textbf{entailed} & \text{if } t \in \mathrm{cl}(G) \\[4pt] \textbf{contradicted} & \text{if } (p,\, \texttt{rdf:type},\, \texttt{owl:FunctionalProperty}) \in \mathrm{cl}(G) \,\wedge\, \exists\, o' \neq o.\ (s, p, o') \in \mathrm{cl}(G) \\ & \text{or } p = \texttt{owl:sameAs} \,\wedge\, (s,\, \texttt{owl:differentFrom},\, o) \in \mathrm{cl}(G) \\[4pt] \textbf{unknown} & \text{otherwise} \end{cases}
```
1値プロパティ(出生地・首都等)を `owl:FunctionalProperty` として宣言することで、異なる値の主張が contradicted として検出可能になる。functional 衝突の判定は「異なる項は異なる個体を指す」という固有名仮定(UNA)を採用する — ただし $`o`$ と $`o'`$ が `owl:sameAs` で同一視される場合は閉包への伝播により第1分岐(entailed)が先に成立するため、誤検出は生じない。矛盾検出は健全側に不完全である: 上記2形以外の矛盾(OWL-RL で表現できない衝突)は unknown に落ち、「矛盾」と誤って断定されることはない。

**CSP(csp.py)** — 有限領域制約充足。インスタンス $`(X, D, C)`$(変数 $`X = (x_1, \dots, x_n)`$、有限領域 $`D_i`$、制約集合 $`C`$)に対し、解集合

```math
\mathrm{Sol} = \{\, v \in D_1 \times \cdots \times D_n \mid \forall c \in C.\ v \models c \,\}
```
を厳密に列挙する(python-constraint による実装。解数が上限を超える場合は打ち切りを明示)。$`\mathrm{Sol} = \emptyset`$(解なし)も確定的に報告される。

**SMT(smt.py)** — Z3 による数値・論理主張の証明。仮定の連言 $`\Gamma`$ と目標 $`\varphi`$ に対し

```math
\mathrm{smt\_verify}(\Gamma, \varphi) = \begin{cases} \textbf{proved} & \text{if } \Gamma \wedge \neg\varphi \text{ is unsat (hence } \Gamma \models \varphi \text{)} \\ \textbf{counterexample } m & \text{if } m \models \Gamma \wedge \neg\varphi \end{cases}
```
を返す。反例 $`m`$ は具体的な変数割当であり、そのまま回帰テストの入力に変換できる。目標を与えない場合は $`\Gamma`$ の充足判定(モデル探索)として働く。

**検証の形式的性質(条件付き健全性)** — 自然言語主張 $`a`$ は LLM 定式化器 $`f_\theta`$ によって記号表現 $`t = f_\theta(a)`$(または $`(\Gamma, \varphi)`$)に写像され、判定は $`V(G, t)`$ が行う。$`V`$ と $`\mathrm{cl}`$ は決定論的なので、**$`f_\theta(a)`$ が $`a`$ を忠実に表現しているならば、判定は $`\mathrm{cl}(G)`$ に関して正しい**。したがってエンドツーエンドの誤りは

```math
\Pr[\mathrm{error}] \;\le\; \underbrace{\Pr[f_\theta(a) \not\simeq a]}_{\text{formalization}} \;+\; \underbrace{\Pr[\mathrm{inexpressible}]}_{\text{symbolic layer}}
```
と上から抑えられる(第1項は定式化の不忠実、第2項は記号層の表現力不足)。すなわち確率的な誤りは定式化層に局在する。これは SatLM (Ye et al., 2023) の「パースされた仕様に対する解の正しさ」保証と同型であり、§5 の層別誤り分析の理論的根拠である。

### 2.3 エージェントループとツール境界

メインエージェントは in-process MCP サーバ経由で9種の記号ツール(`kb_add_triples`, `kb_find`, `kb_sparql`, `kb_verify`, `kb_infer`, `kb_stats`, `kb_provenance`, `csp_solve`, `smt_verify`)と `Task`(サブエージェント委譲)のみを許可される(`permission_mode="dontAsk"` + allowed_tools)。通常モードではファイルシステム・bash・ネットワークはツール境界で遮断され、エージェントが記号層を迂回して外界に触れる経路は存在しない。この閉じた構成は、実験における条件統制(§3.1)にも直接利用される。

### 2.4 サブエージェント委譲

ツールコールの嵩む探索・監査は `Task` ツールで専門サブエージェントに委譲される。各サブエージェントは定義されたツールのみを持ち、試行錯誤は自身の文脈内に閉じ、蒸留された結論だけがメインの文脈に戻る。

| サブエージェント | モデル | ツール | 役割 |
|---|---|---|---|
| symbolic-explorer | 小型(haiku) | kb_*(読み取り専用) | 知識グラフのマルチホップ探索。全トリプル検証済みの根拠パスを返す |
| kb-auditor | 小型(haiku) | kb_*(読み取り専用)+ provenance | KB 全域の矛盾走査。食い違う情報源のペアまで特定する |
| prover | 継承 | smt_verify, kb_find/sparql | 大きな主張を補題に分解し、SMT で積み上げ証明 |
| test-generator※ | 継承 | smt_verify, csp_solve, Read | 反例・境界値・同値クラス代表値から pytest テストを系統的に生成 |
| loop-judge※ | 継承 | kb_*, smt_verify, Bash | ループ終了条件を機械判定可能な述語の合取として KB に記録し、毎イテレーション DONE / CONTINUE / STALLED を決定論的に判定 |

※ はコーディングモード限定。explorer / auditor に小型モデルを既定とするのは、「決定論的なツールの上を歩く作業に大きなモデルは不要」という仮説(RQ5)に基づく設計判断であり、§4.5 で検証する。

### 2.5 静的解析による KB 構築(code2kb)

コードベースの構造的事実 — モジュールの import、関数呼び出し、クラス継承、定義位置 — は Python `ast` により決定論的に抽出され、LLM を介さず KB に格納される。LLM 抽出(`ingest`)は契約・意図・設計判断といった静的解析では取れない意味層を担当する。この分業により、構造層は網羅的かつ再現可能であり(RQ6)、下流の影響分析(「関数 X を変更すると壊れうる呼び出し元は?」)は symbolic-explorer が構造層の上を決定論的に辿ることで答えられる。

### 2.6 ハイブリッドコーディングモード

`nsai code` はファイル操作ツール(Read/Glob/Grep/Edit/Write)をニューラル層に許可し、bash はコマンドごとのユーザー確認ゲート付きで開放する(実験・サンドボックス用に確認を省く `--full-auto`、全許可の `--bypass-permissions` を持つ)。コーディング作業自体は通常のエージェントと同様にニューラル層が行い、記号層は次の検証チェックポイントとして機能する:

1. **入力検証** — 既存バリデーションを通過しつつ事前条件を破る値の存在を `smt_verify` で探索。反例は具体的なエッジ入力であり、ガード追加と回帰テスト化に直結する。
2. **出力検証** — 実装後に「このガード下では返り値は必ず区間 [0, len−1] 内」等の主張を Z3 で証明。反証されれば反例がテストケースになる。
3. **境界・オフバイワン監査** — ループ範囲・ページネーション・日付演算を整数制約として符号化し、目視でなく証明で確認する。
4. **プロジェクト記憶** — 証明済みインバリアントや API 契約を出典付きで KB に蓄積し、後の変更が記録と矛盾すれば `contradicted` として検出する。

---

## 3. Experimental Setup

4つの研究課題を検証する。RQ1: 記号層による検証は偽検証率を LLM 単体より大幅に下げるか。RQ2: KB 規模の増大に対して long-context ベースラインは劣化するが記号層接地は劣化しないか。また探索委譲には損益分岐点があるか。RQ3: 系統的な矛盾走査は文脈読解による矛盾発見より高精度か。RQ4: SMT 組み込みコーディングは境界バグの検出・修正・テスト品質を改善するか。副次的に RQ5(小型モデル委譲のコスト効率)、RQ6(静的解析構造層の寄与)を分析する。

### 3.1 比較条件

| 条件 | 構成 |
|---|---|
| **B0** | LLM 単体(ツールなし・事実を与えない)。パラメトリック知識の限界と汚染プローブ |
| **B1** | LLM + 全事実(KB の全トリプルを Turtle のまま)をプロンプトに同梱する long-context ベースライン。最も強い比較対象 |
| **C1** | nsai(記号ツールあり・サブエージェント委譲なし)。記号層そのものの寄与 |
| **C2** | nsai フル(委譲あり)。委譲の追加寄与はアブレーション C2−C1 で測る |

C1/C2 の実装差分はエージェント構成の `subagents` フラグのみであり、他の交絡はない。B1 が文脈長制約で全トリプルを同梱できない場合(MetaQA)、質問エンティティの 2-hop 近傍のみを同梱するオラクル検索付き long-context(B1′)に置き換える — これは実務の RAG 構成に対応する。

**モデルと反復**: 主系は sonnet、代表設定のみ opus / haiku で追試する。合成データは各設定 5 run、公開ベンチマークは 3 run の平均±標準偏差を報告する。同一問題セットに対する対応あり比較のため、2値正誤には McNemar 正確検定、連続値には paired bootstrap を用いる(有意水準 5%)。全実行についてハーネスが正誤ラベル・コスト・トークン数・ツールコール数(種類別)・ターン数・壁時計時間を JSONL に記録する。

### 3.2 データセット

**合成 KB(制御実験)** — 生成器 `kbgen` は家系・組織・地理ドメインのスキーマから、規模・ホップ数・矛盾数を独立制御した KB と、QA ペア・検証主張・注入矛盾を同時生成する(シード固定)。**全 gold ラベルは生成時に OWL-RL 閉包で検証される**ため、データセットと `kb_verify` が食い違うことは構造的にない。難易度制御・スケーリング曲線・損益分岐の分析はこちらで行い、汚染はゼロである。

**公開ベンチマーク(外的妥当性)** — KB・トリプル・ルールが明示的に付属するものを優先する(テキストのみのベンチマークは検索・抽出性能が交絡するため)。実験1: ProofWriter (Tafjord et al., 2021) / PrOntoQA (Saparov and He, 2023)(ルールベース演繹 — 3値判定と同型)、FEVER (Thorne et al., 2018) dev 300 件(根拠文を ingest してからの抽出込み評価)。実験2: MetaQA (Zhang et al., 2018)(約13万トリプル KB 付き 1/2/3-hop QA)、CLUTRR (Sinha et al., 2019)(構成的汎化)、2WikiMultiHopQA (Ho et al., 2020) 300 問(根拠トリプル付きフルパイプライン評価)。実験4: QuixBugs (Lin et al., 2017)、HumanEval+ / MBPP+(EvalPlus; Liu et al., 2023 — 原典は HumanEval (Chen et al., 2021) と MBPP (Austin et al., 2021))、LiveCodeBench (Jain et al., 2025)(カットオフ後問題)、SWE-bench (Jimenez et al., 2024) Verified (Chowdhury et al., 2024) 境界バグサブセット。ProofWriter のルールは rdfs:subClassOf / SPARQL に写像し、写像不能項目は除外して除外率を報告する。

**汚染対策** — 3対策を全公開ベンチマークに適用する: (1)B0 を汚染プローブとして常時報告し、B0 正解項目を除いた uncontaminated サブセットを主表とする(全項目版は付録)。(2)B1/C1/C2 は同一の事実集合を見るため汚染は全条件に同方向に働き、条件間差分は依然有効 — 主張の根拠は絶対値でなく差分に置く。(3)MetaQA / CLUTRR にはエンティティ名を無作為固有名に置換した摂動版(KB ごと置換するため正解は保存)を用意し、原版とのギャップを LLM 記憶依存度の測定として報告する。記号層は置換に不変のため C1/C2 のギャップ ≈ 0 が予測となる。

### 3.3 指標と統計検定の形式的定義

タスク集合を $`i \in \{1, \dots, N\}`$、gold ラベルを $`y_i`$、システムの予測を $`\hat{y}_i`$ と書く。3値判定のラベル集合は $`\Lambda = \{\textsf{e}, \textsf{c}, \textsf{u}\}`$(entailed / contradicted / unknown)。

**macro-F1(3値判定)** — ラベル $`\ell \in \Lambda`$ ごとに適合率 $`P_\ell = \mathrm{TP}_\ell / (\mathrm{TP}_\ell + \mathrm{FP}_\ell)`$、再現率 $`R_\ell = \mathrm{TP}_\ell / (\mathrm{TP}_\ell + \mathrm{FN}_\ell)`$、$`F1_\ell = 2 P_\ell R_\ell / (P_\ell + R_\ell)`$ とし、

```math
\text{macro-F1} = \frac{1}{|\Lambda|} \sum_{\ell \in \Lambda} F1_\ell
```
**偽検証率(FVR; 本論文の最重要指標)** — 正解が contradicted または unknown の主張を「正しい(entailed)」と断定した割合:

```math
\mathrm{FVR} = \frac{\left|\{\, i : y_i \in \{\textsf{c}, \textsf{u}\} \wedge \hat{y}_i = \textsf{e} \,\}\right|}{\left|\{\, i : y_i \in \{\textsf{c}, \textsf{u}\} \,\}\right|}
```
これをハルシネーションの操作的定義とする。誤りの向きを区別しない accuracy と異なり、FVR は「誤って検証済みと断定する」失敗のみを数える。

**Exact Match(QA)** — $`\mathrm{EM} = \frac{1}{N} \sum_i \mathbb{1}[\hat{y}_i = y_i]`$。表記揺れを排除するため回答は CURIE で返させ、文字列正規化後の完全一致で判定する。

**矛盾検出(実験3)** — 注入した矛盾の集合を $`D`$、システムが報告した集合を $`\hat{D}`$ とし、$`P = |D \cap \hat{D}| / |\hat{D}|`$、$`R = |D \cap \hat{D}| / |D|`$、$`F1`$ はその調和平均。出典ペア特定率は、正しく検出された矛盾 $`D \cap \hat{D}`$ のうち、食い違う情報源の対まで正しく特定できたものの割合。

**根拠パス検証可能率(実験2)** — explorer が回答とともに返す根拠パス $`(t_1, \dots, t_k)`$ に対し $`\frac{1}{k} \sum_j \mathbb{1}[V(G, t_j) = \textsf{e}]`$ を全質問で平均する。設計目標は 1.0(サブエージェントの報告が全トリプル機械検証可能であること)。

**委譲の損益分岐(実験2)** — KB 規模 $`n = |G|`$ における条件 $`X`$ のコストを $`\mathrm{cost}_X(n)`$、精度を $`\mathrm{EM}_X(n)`$ とし、損益分岐点を

```math
n^* = \min \{\, n : \mathrm{cost}_{C2}(n) \le \mathrm{cost}_{C1}(n) \,\wedge\, \mathrm{EM}_{C2}(n) \ge \mathrm{EM}_{C1}(n) \,\}
```
と定義する(委譲のオーバーヘッドが探索ツールコールの節約で回収される最小規模)。

**Mutation score(実験4b)** — 生成された変異体の集合を $`M`$、テストスイートが検出(kill)した部分集合を $`K \subseteq M`$ とし $`\mathrm{MS} = |K| / |M|`$。境界変異(`<` ↔ `<=`、$`\pm 1`$ 定数等)の部分集合 $`M_{\mathrm{bd}} \subseteq M`$ に限定した $`\mathrm{MS}_{\mathrm{bd}} = |K \cap M_{\mathrm{bd}}| / |M_{\mathrm{bd}}|`$ を別掲する。

**早期完了率(実験4c)** — 完了宣言の総数のうち、宣言時点で隠しテストが失敗しているものの割合。

**統計検定** — 同一問題セットに対する対応あり比較を行う。2値正誤には McNemar 正確検定を用いる: 条件 A のみ正解の件数を $`n_{10}`$、条件 B のみ正解の件数を $`n_{01}`$ とし、帰無仮説(両条件の誤り確率が等しい)の下で $`n_{10} \sim \mathrm{Bin}(n_{10} + n_{01},\, \tfrac{1}{2})`$ となることから正確両側 $`p`$ 値を計算する(一致対は寄与しない)。連続値(コスト・F1 等)には paired bootstrap を用いる: タスク添字を復元抽出で $`B = 10^4`$ 回再標本化し、条件間差 $`\Delta_b`$ の経験分布から $`p`$ 値と 95% 信頼区間を得る。有意水準はいずれも 5%。

### 3.4 実験1: 主張検証とハルシネーション抑制(RQ1)

KB に対する主張の3値判定(entailed / contradicted / unknown)。合成データは各ラベル 100 問: entailed は明示トリプル 50 + **推論でのみ導ける** 50(subClassOf 連鎖、functional+sameAs)、contradicted は functional 衝突 50 + differentFrom 衝突 50、unknown は KB が沈黙する尤もらしい主張 100。指標は macro-F1 と偽検証率 FVR(定義は §3.3)。unknown の正答率は独立に報告する(LLM は unknown を苦手とし当て推量する、が仮説)。

### 3.5 実験2: マルチホップ QA と委譲の損益分岐(RQ2)

中間エンティティを要する質問への回答。合成 KB を規模 {100, 500, 2000, 5000} トリプル × ホップ数 k ∈ {2, 3, 4, 6} で生成し(各 50 問)、経路が事前に自明でない質問を半数含める。公開系は MetaQA(1/2/3-hop 各 200 問)・CLUTRR・2Wiki。回答は CURIE で返させ Exact Match で採点する。分析は(1)EM のホップ数 × KB 規模マトリクス、(2)B1 の劣化曲線 vs C1/C2 の平坦さ、(3)C1 vs C2 のコスト・レイテンシ・EM による委譲の損益分岐 $`n^*`$(定義は §3.3)、(4)explorer が報告した根拠パスの検証可能率(各トリプルを機械的に $`V(G, \cdot)`$ に通す — 設計目標 1.0)。C2 では委譲が実際に起きたかをツールログから確認し委譲率も報告する。

### 3.6 実験3: 矛盾検出の精度(RQ3)

クリーンな合成 KB(500 / 2000 トリプル)に矛盾を c ∈ {5, 20} 件注入し(functional 衝突、sameAs/differentFrom 衝突、および推論を経てのみ衝突する subClassOf 経由のケースを半数)、注入時に相異なる出典を provenance に記録する。B1(全トリプル+全出典ログを文脈で読ませる)/ C1 / C2(kb-auditor 委譲)で、検出の Precision / Recall / F1、出典ペア特定率、コストを比較する。KB 規模増大時の Recall の低下(B1 は文脈長で崩れる、が仮説)を報告する。

### 3.7 実験4: ハイブリッドコーディング(RQ4)

**(4a)境界バグの監査・修正** — QuixBugs(Python 40 問)+ 自作境界バグスイート 30 問(ページネーション・区間演算・インデックス・日付・丸め。半数は LiveCodeBench カットオフ後問題の正解実装にバグを植えた汚染フリー構成、隠しテストを別途保持)。素のコーディングエージェント(ファイルツールのみ)vs `nsai code`(SMT あり)で、バグ検出率・修正後の隠しテスト通過率・SMT 反例の回帰テスト化率を測る。

**(4b)テスト生成の変異検出力** — EvalPlus の正解実装から数値・境界ロジック中心の 40 関数 + 自作 10 関数。LLM 直書きテスト vs test-generator を mutation testing(mutmut)の mutation score で比較し、境界変異(`<` ↔ `<=`、±1)限定スコアを別掲する。テスト数を揃えた比較(上位 n 件)も併記する。

**(4c)loop-judge と完了判定** — 「全テスト成功+宣言済み主張の証明」が完了条件の修正タスク 15 問(うち 5 問は仕様が矛盾する完了不能タスク)。LLM 自身の完了判定 vs loop-judge で、早期完了率(完了宣言時に隠しテストが失敗している率)、完了不能タスクでの STALLED 検出率、平均イテレーション数を比較する。

**(4d)SWE-bench Verified 境界バグサブセット(副次)** — gold パッチが境界・数値条件の小変更(比較演算子の変更、±1 定数、min/max・範囲チェック、日付/インデックス演算)であるタスクをパッチの AST 差分による機械フィルタで 30–50 問抽出する(フィルタ条件とヒット数を報告)。`nsai code` vs 記号ツールのみを除いたアブレーションで、SWE-bench 公式ハーネスによる解決率と smt_verify の実使用率を測る。**条件間差分のみ**を主張に使い、絶対スコアは参考値とする。

### 3.8 副次実験(RQ5, RQ6)

**RQ5**: 実験2・3の C2 で explorer/auditor を haiku → sonnet に変えた条件を追加し、コスト×精度のパレートを描く。**RQ6**: OSS リポジトリ 3–5 件に対し、(a)code2kb と LLM 抽出のエッジ再現率の突き合わせ(ast 出力を正解系とする)、(b)影響分析 20 問を構造層あり/なしで explorer に解かせた正答率比較。

---

## 4. Results

> **注: 本実行の数値・表・図は実験実施後に記入する(現在ブランク)。§4.0 のパイロット結果のみ記入済み。**

### 4.0 パイロット実行(予備結果)

本実行に先立ち、メトリクス定義・プロンプト・構造化抽出の検証を目的とするパイロットを実施した(週1マイルストーン)。設定: 合成 KB 479 トリプル(シード 42)、3値主張判定 10 問 + マルチホップ QA 10 問、モデルは haiku、各条件 1 run(n=20/条件)。

**表0: パイロット結果(n=20、haiku、1 run — 予備値であり主張の根拠には用いない)**

| 条件 | 主張判定 正答率 (n=10) | QA EM (n=10) | 偽検証率 | コスト/問 | 秒/問 | ツールコール/問 |
|---|---|---|---|---|---|---|
| B0 | 0.30 | 0.00 | 0.00 | \$0.003 | 7.9 | 0 |
| B1 | 1.00 | 1.00 | 0.00 | \$0.026 | 11.8 | 0 |
| C1 | 1.00 | 1.00 | 0.00 | \$0.021 | 18.0 | 3.8 |
| C2 | 1.00 | 1.00 | 0.00 | \$0.013 | 13.6 | 3.3 |

予備的な観察(いずれも n が小さく、本実行で再検証する):

1. **汚染ゼロの確認**: B0(事実を与えない)の正解は gold が unknown の3問のみで、QA は全問不正解 — 合成 KB の無作為エンティティはパラメトリック知識で解けないことを確認した。また B0 は誤った主張を「検証済み」と断定せず一貫して unknown 側に倒れた(偽検証率 0)。この保守性がモデル・規模・推論必須問題でも維持されるかが RQ1 本実行の焦点となる。
2. **479 トリプルでは B1 も天井**: B1/C1/C2 は全問正解。この規模では long-context ベースラインが十分機能しており、仮説どおりなら B1 の劣化は KB 規模スケーリング(実験2)で現れる。条件間の差はまずコスト構造に出た: KB 全文同梱の B1 は主張判定で \$0.025/問 に対し、必要な照合だけをツールで行う C1 は \$0.015、委譲する C2 は \$0.008 と最安。一方レイテンシはツール往復分 C1/C2 が長い。
3. **ハーネスの改善点**: B0 で構造化抽出に失敗した回答が1件あり(20×4条件中)、回答フォーマット指示を強化した。本パイロットの結果をもってメトリクス定義を凍結した。

### 4.1 主張検証とハルシネーション抑制(RQ1)

**表1: 3値主張判定(合成 KB、各ラベル 100 問、5 run 平均±SD)**

| 条件 | macro-F1 | 偽検証率 ↓ | unknown 正答率 | entailed(推論必須)正答率 | コスト/問 |
|---|---|---|---|---|---|
| B0 | TBD | TBD | TBD | TBD | TBD |
| B1 | TBD | TBD | TBD | TBD | TBD |
| C1 | TBD | TBD | TBD | TBD | TBD |
| C2 | TBD | TBD | TBD | TBD | TBD |

**表2: 公開ベンチマーク(ProofWriter / ProntoQA depth 別、FEVER)** — TBD(uncontaminated サブセットを主表、全項目版は付録)

主要な観察(記入予定):

- B0/B1 の偽検証率と推論必須問題・unknown での挙動: TBD
- C1 の誤りの内訳(定式化ミスへの局在): TBD → §5
- McNemar 検定の結果: TBD

### 4.2 マルチホップ QA とスケーリング(RQ2)

**図2: KB 規模 × Exact Match(ホップ数別)** — TBD(B1 の劣化曲線 vs C1/C2 の平坦さ)

**表3: MetaQA 1/2/3-hop EM(既存 KB-QA / LLM 系手法との比較)** — TBD

**表4: 委譲の損益分岐(C1 vs C2: EM・コスト・レイテンシを KB 規模別に)** — TBD

- 委譲率(メインが委譲基準に従った割合): TBD
- explorer 根拠パスの全トリプル検証可能率(設計目標 100%): TBD
- 摂動版とのギャップ(LLM 記憶依存度; C1/C2 は ≈ 0 が予測): TBD

### 4.3 矛盾検出(RQ3)

**表5: 矛盾検出の Precision / Recall / F1・出典ペア特定率(KB 規模 × 注入数)** — TBD

- 推論を経てのみ衝突するケースでの条件間差: TBD
- KB 規模増大時の B1 Recall の低下: TBD

### 4.4 ハイブリッドコーディング(RQ4)

**表6: 境界バグ監査・修正(4a: QuixBugs + 自作スイート)** — TBD(検出率、隠しテスト通過率、反例の回帰テスト化率)

**表7: テスト生成の mutation score(4b: 全変異 / 境界変異限定 / テスト数統制)** — TBD

**表8: 完了判定(4c: 早期完了率、STALLED 検出率、平均イテレーション数)** — TBD

**表9: SWE-bench Verified 境界バグサブセット(4d: 解決率差分、smt_verify 実使用率)** — TBD

### 4.5 副次分析(RQ5, RQ6)

**図3: 委譲モデル別のコスト×精度パレート(haiku vs sonnet explorer/auditor)** — TBD

**表10: code2kb vs LLM 抽出のエッジ再現率、影響分析正答率** — TBD

---

## 5. Error Analysis

> **注: 数値は実験後に記入。本節では誤りを層別する分析枠組みを規定する。**

C1/C2 の誤答集合 $`E`$ を、互いに素な3層 $`E = E_{\mathrm{form}} \uplus E_{\mathrm{deleg}} \uplus E_{\mathrm{solv}}`$ に分割し、各層の比率 $`|E_{\bullet}| / |E|`$ を報告する。§2.2 の条件付き健全性より、記号層が決定論的である限り $`E_{\mathrm{form}}`$ と $`E_{\mathrm{solv}}`$ で誤りは尽きるはずであり、$`E_{\mathrm{deleg}}`$ はエージェント統合に固有の追加誤り源である。分類は2名(または LLM 判定 + 人手検証)で行い一致率を報告する:

1. **定式化層 $`E_{\mathrm{form}}`$(ニューラル)** — LLM が自然言語を誤った記号表現に写像した($`f_\theta(a)`$ が $`a`$ に不忠実: 誤ったトリプル分解、誤った SPARQL、誤った SMT 符号化)。記号層は与えられた式を正しく評価している。
2. **委譲層 $`E_{\mathrm{deleg}}`$(エージェント)** — 委譲すべき場面で委譲しなかった、またはサブエージェントの結論を誤って統合した。
3. **ソルバー層 $`E_{\mathrm{solv}}`$(記号)** — 表現力の限界(OWL-RL で表せない含意、写像除外)またはタイムアウト。

**仮説**: 記号層導入後に残る誤りは定式化層にほぼ局在する($`|E_{\mathrm{form}}| / |E|`$ = TBD %)。これが確認されれば、「検証の信頼性問題が、より小さく検査可能な定式化の正しさ問題に還元された」ことになり、本アーキテクチャの最も強い証拠となる。定式化ミスの下位分類(語彙選択、方向の取り違え、量化の誤り等)と代表例: TBD。

委譲不履行が観測された場合は、委譲を強制した条件 C2′ を追加し、方針遵守の問題と上限性能を分離する: TBD。

---

## 6. Related Work

**ニューロシンボリック AI.** 記号推論と学習の統合は長い系譜を持ち、Kautz (2022) の taxonomy や Garcez and Lamb (2023)、Sarker et al. (2021) のサーベイに整理されている。Kautz の分類では本システムは Neuro[Symbolic] — ニューラルなエージェントが記号推論エンジンを内部サービスとして呼ぶ構成 — に位置づく。本研究の貢献はこの構成を LLM エージェントにおいて徹底し(事実の保存・推論・検証・求解のすべてを記号層に委譲)、その利得を測定可能な形で示すことにある。

**Tool-augmented LLM とプログラム補助推論.** 外部ツールで LLM を補強する枠組みとして Toolformer (Schick et al., 2023) や ReAct (Yao et al., 2023) がある。ReAct は外部 API との相互作用が chain-of-thought のハルシネーションを軽減すると報告するが、その外部層は検索であり推論・検証は LLM 側に残る。推論を実行系に外部化する PAL (Gao et al., 2023) と Program of Thoughts (Chen et al., 2023) は「分解・定式化だけを LLM の仕事として残し、求解はインタプリタに委譲する」分業を明示的に定式化した原典であり、本研究はこの分業原理を汎用インタプリタから形式的記号層(OWL-RL / CSP / SMT)へ一般化し、計算のみならず**事実の保存と主張の判定そのもの**を記号層に移す。

**LLM + 形式論理ソルバー.** Logic-LM (Pan et al., 2023)、LINC (Olausson et al., 2023)、SatLM (Ye et al., 2023) は、LLM を自然言語から形式表現への翻訳器としてのみ用い、推論は決定論的ソルバー(それぞれ記号ソルバー群、一階述語論理証明器、Z3)が行う。Proof of Thought (Ganguly et al., 2024) も同型の分業を DSL 経由の Z3 検証として実現する。本研究はこの「LLM=翻訳器、ソルバー=推論器」の分業を単発の QA から**永続的な KB を持つエージェント**に拡張し、出典管理・矛盾検出・マルチセッションの知識蓄積・コーディング検証までを単一アーキテクチャで扱う。また誤りが翻訳(定式化)層に局在するという同系研究の観察を、エージェント設定で層別誤り分析として定量化する(§5)。

**知識グラフと RAG.** LLM と KG の統合は Pan et al. (2024) が KG-enhanced LLM / LLM-augmented KG / 相乗型の3枠組みに整理している。KBQA の系譜(MetaQA 上の代表手法として EmbedKGQA; Saxena et al., 2020)、GraphRAG (Edge et al., 2024)、および KG 上をエージェントが探索する Think-on-Graph (Sun et al., 2024) は、構造化知識による LLM の接地を目指す。しかし多くの構成では、検索されたトリプルを文脈に注入して最終判断を LLM に委ねる(Think-on-Graph では探索・枝刈り・推論の主体が LLM であり、GraphRAG では KG 自体が LLM の生成物である)。これに対し本研究では判定自体(entailed/contradicted/unknown)を OWL-RL 閉包上の決定論的照合が行い、LLM は判定結果を報告する側に回る。B1(long-context)/ B1′(オラクル検索)ベースラインとの比較はこの設計差の効果を直接測る。

**自己検証とハルシネーション検出.** Self-Consistency (Wang et al., 2023)、SelfCheckGPT (Manakul et al., 2023)、Chain-of-Verification (Dhuliawala et al., 2024) 等の self-verification 系は、検証器も確率的であるという循環性を持つ(CoVe の4段階検証はすべて LLM 内部で完結する)。本研究の kb_verify / smt_verify は被検証器と機構を共有しない外部の決定論的検証器であり、偽検証率(§3.3)はこの差を測る操作的指標である。FEVER (Thorne et al., 2018) 系の事実検証タスクは3値ラベルが本システムの判定と同型であるため、抽出込みのエンドツーエンド評価に用いる。

**LLM とソフトウェア検証.** LLM による形式検証支援として、ループ不変条件の生成・ランキング (Chakraborty et al., 2023; Bharti et al., 2025) や形式仕様の自動生成 (SpecGen; Ma et al., 2025) が研究されており、いずれも「LLM 生成物を決定論的検証器が裁定する」構図を持つ。本研究のハイブリッドコーディングモードは、完全な形式検証ではなく「危ういロジックの局所的な証明/反証」という軽量な統合点を選び、SMT の反例を回帰テストへ直結させる点、およびループ終了条件の形式化による完了判定(loop-judge)を含む点に特徴がある。

**近接する同時代研究.** 本研究に最も近いのは次の2系統である。(i)**LLM+複数ソルバー**: MCP-Solver (Szeider, 2025) は MCP 経由で LLM を MiniZinc・PySAT・Z3 に接続し、「LLM が定式化、ソルバーが求解」の分業と複数ソルバー統合を実現する。ただしソルバーへのツールアクセス層であり、永続 KB・出典管理・検証を組み込んだエージェントループを持たない。(ii)**KG 上の LLM エージェント**: SymAgent (Liu et al., 2025) は KG を動的環境とみなし LLM エージェント(Planner/Executor)が多段推論するが、推論の主体は LLM であり決定論的推論器には委譲しない。また永続 KG をエージェントメモリとする Zep (Rasmussen et al., 2025) や A-MEM (Xu et al., 2025) は保存を構造化するが、記憶の構築・検索・整合性判断が LLM/埋め込みに依存し、決定論的な含意・矛盾判定を持たない。整理すると、メモリ系は「保存はするが推論・検証が LLM 依存」、ソルバー・検証系は「検証はするが単発タスクで永続知識がない」— 本研究は両者(永続 KB + 決定論的判定 + 複数ソルバー + 委譲)を単一エージェントに統合し、その寄与をアブレーションで分離する点で異なる。

---

## 7. Limitations

- **単一 LLM ファミリー**: 実験は Claude ファミリー(sonnet 主系、opus/haiku 追試)に限られる。分業アーキテクチャ自体はモデル非依存だが、定式化性能の絶対値は移らない可能性がある。
- **閉世界の KB**: kb_verify の contradicted / unknown は KB に対する判定であり、KB 自体の正しさ・網羅性は保証しない。誤った事実が出典付きで格納されれば、システムは一貫して誤る(garbage in, consistent garbage out)。出典ログはこの監査可能性を担保する装置であって、真実性の保証ではない。
- **表現力の限界**: OWL-RL で表せない含意(存在量化、数値範囲推論等)は判定不能であり、ProofWriter 等の写像では除外項目が生じる(除外率は報告する: TBD)。SMT 符号化も LLM の定式化に依存し、符号化しにくい性質(浮動小数の実挙動、並行性)には及ばない。
- **スケーラビリティ**: OWL-RL 閉包の計算コストは KB 規模に対して非自明であり、13万トリプル規模(MetaQA)では閉包キャッシュまたは推論なし直照合へのフォールバックを要した(詳細: TBD)。
- **公開ベンチマークの汚染**: 3重の対策(B0 プローブ、条件間差分、摂動版)を適用したが、汚染の完全な排除は不可能である。主張は条件間差分と合成データの制御実験に基づける。
- **コーディング評価の範囲**: 実験4の主張は境界・数値条件ロジックに限定され、総合的なソフトウェア開発能力には及ばない(§1.1)。

---

## 8. Conclusion

LLM を定式化器に限定し、事実の保存・推論・検証・求解を決定論的な記号層に委ねる分業アーキテクチャを提案し、エージェント `nsai` として実装した。合成 KB による制御実験と KB 付き公開ベンチマークでの評価により、偽検証率の低減(TBD)、KB 規模に対する頑健性(TBD)、矛盾検出精度(TBD)、境界バグの検出・修正とテスト品質の改善(TBD)を示した。残存誤りが定式化層に局在するという分析(TBD)は、「LLM の検証をどう信じるか」という問題を「LLM の翻訳をどう検査するか」というより小さな問題に還元できることを示唆する。今後の課題として、閉包計算の増分化による大規模 KB への拡張、OWL-RL を超える表現力(数値範囲・存在量化)の段階的導入、他 LLM ファミリーへの一般化検証が挙げられる。

---

## References

全書誌は一次情報源(公式プロシーディングス・ACL Anthology・arXiv・DBLP・Crossref)で検証済み(2026-07-07 時点)。

- Austin, J., Odena, A., Nye, M., Bosma, M., Michalewski, H., Dohan, D., Jiang, E., Cai, C., Terry, M., Le, Q., Sutton, C. (2021). Program Synthesis with Large Language Models. arXiv:2108.07732 [preprint].
- Bharti, V., Jha, S., Kumar, D., Jalote, P. (2025). Loop Invariant Generation: A Hybrid Framework of Reasoning optimised LLMs and SMT Solvers. arXiv:2508.00419 [preprint, under review].
- Chakraborty, S., Lahiri, S. K., Fakhoury, S., Musuvathi, M., Lal, A., Rastogi, A., Senthilnathan, A., Sharma, R., Swamy, N. (2023). Ranking LLM-Generated Loop Invariants for Program Verification. Findings of EMNLP 2023. arXiv:2310.09342.(著者順は ACL Anthology 版に従う)
- Chen, M., Tworek, J., Jun, H., Yuan, Q., et al. (2021). Evaluating Large Language Models Trained on Code. arXiv:2107.03374 [preprint].(HumanEval)
- Chen, W., Ma, X., Wang, X., Cohen, W. W. (2023). Program of Thoughts Prompting: Disentangling Computation from Reasoning for Numerical Reasoning Tasks. TMLR 2023. arXiv:2211.12588.
- Chowdhury, N., Aung, J., Shern, C. J., et al. (2024). Introducing SWE-bench Verified. OpenAI. https://openai.com/index/introducing-swe-bench-verified/ [Web 資料; 著者欄は投稿前に原典ページで要目視確認]
- Dhuliawala, S., Komeili, M., Xu, J., Raileanu, R., Li, X., Celikyilmaz, A., Weston, J. (2024). Chain-of-Verification Reduces Hallucination in Large Language Models. Findings of ACL 2024, pp. 3563–3578. arXiv:2309.11495.
- Edge, D., Trinh, H., Cheng, N., Bradley, J., Chao, A., Mody, A., Truitt, S., Metropolitansky, D., Ness, R. O., Larson, J. (2024). From Local to Global: A Graph RAG Approach to Query-Focused Summarization. arXiv:2404.16130 [preprint; 投稿前に venue 再確認].
- Ganguly, D., Iyengar, S., Chaudhary, V., Kalyanaraman, S. (2024). Proof of Thought: Neurosymbolic Program Synthesis allows Robust and Interpretable Reasoning. NeurIPS 2024 System-2 Reasoning At Scale Workshop. arXiv:2409.17270.
- Gao, L., Madaan, A., Zhou, S., Alon, U., Liu, P., Yang, Y., Callan, J., Neubig, G. (2023). PAL: Program-aided Language Models. ICML 2023, PMLR 202:10764–10799. arXiv:2211.10435.
- Garcez, A. d'A., Lamb, L. C. (2023). Neurosymbolic AI: the 3rd wave. Artificial Intelligence Review, 56(11), 12387–12406. arXiv:2012.05876. DOI 10.1007/s10462-023-10448-w.
- Ho, X., Duong Nguyen, A.-K., Sugawara, S., Aizawa, A. (2020). Constructing A Multi-hop QA Dataset for Comprehensive Evaluation of Reasoning Steps. COLING 2020, pp. 6609–6625. arXiv:2011.01060.(2WikiMultiHopQA)
- Jain, N., Han, K., Gu, A., Li, W.-D., Yan, F., Zhang, T., Wang, S., Solar-Lezama, A., Sen, K., Stoica, I. (2025). LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language Models for Code. ICLR 2025. arXiv:2403.07974.
- Jimenez, C. E., Yang, J., Wettig, A., Yao, S., Pei, K., Press, O., Narasimhan, K. (2024). SWE-bench: Can Language Models Resolve Real-World GitHub Issues? ICLR 2024. arXiv:2310.06770.
- Kautz, H. A. (2022). The third AI summer: AAAI Robert S. Engelmore Memorial Lecture. AI Magazine, 43(1), 105–125. DOI 10.1002/aaai.12036.
- Lin, D., Koppel, J., Chen, A., Solar-Lezama, A. (2017). QuixBugs: A Multi-Lingual Program Repair Benchmark Set Based on the Quixey Challenge. SPLASH Companion 2017, pp. 55–56. DOI 10.1145/3135932.3135941.
- Liu, B., Zhang, J., Lin, F., Yang, C., Peng, M., Yin, W. (2025). SymAgent: A Neural-Symbolic Self-Learning Agent Framework for Complex Reasoning over Knowledge Graphs. WWW 2025. arXiv:2502.03283. DOI 10.1145/3696410.3714768.
- Liu, J., Xia, C. S., Wang, Y., Zhang, L. (2023). Is Your Code Generated by ChatGPT Really Correct? Rigorous Evaluation of Large Language Models for Code Generation. NeurIPS 2023. arXiv:2305.01210.(EvalPlus / HumanEval+ / MBPP+)
- Ma, L., Liu, S., Li, Y., Xie, X., Bu, L. (2025). SpecGen: Automated Generation of Formal Program Specifications via Large Language Models. ICSE 2025. arXiv:2401.08807. DOI 10.1109/ICSE55347.2025.00129.
- Manakul, P., Liusie, A., Gales, M. J. F. (2023). SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models. EMNLP 2023, pp. 9004–9017. arXiv:2303.08896.
- Olausson, T. X., Gu, A., Lipkin, B., Zhang, C. E., Solar-Lezama, A., Tenenbaum, J. B., Levy, R. (2023). LINC: A Neurosymbolic Approach for Logical Reasoning by Combining Language Models with First-Order Logic Provers. EMNLP 2023, pp. 5153–5176. arXiv:2310.15164.(Outstanding Paper Award)
- Pan, L., Albalak, A., Wang, X., Wang, W. Y. (2023). Logic-LM: Empowering Large Language Models with Symbolic Solvers for Faithful Logical Reasoning. Findings of EMNLP 2023, pp. 3806–3824. arXiv:2305.12295.
- Pan, S., Luo, L., Wang, Y., Chen, C., Wang, J., Wu, X. (2024). Unifying Large Language Models and Knowledge Graphs: A Roadmap. IEEE TKDE, 36(7), 3580–3599. arXiv:2306.08302. DOI 10.1109/TKDE.2024.3352100.
- Rasmussen, P., Paliychuk, P., Beauvais, T., Ryan, J., Chalef, D. (2025). Zep: A Temporal Knowledge Graph Architecture for Agent Memory. arXiv:2501.13956 [preprint].
- Saparov, A., He, H. (2023). Language Models Are Greedy Reasoners: A Systematic Formal Analysis of Chain-of-Thought. ICLR 2023. arXiv:2210.01240.(PrOntoQA)
- Sarker, M. K., Zhou, L., Eberhart, A., Hitzler, P. (2021). Neuro-symbolic artificial intelligence. AI Communications, 34(3), 197–209. arXiv:2105.05330. DOI 10.3233/AIC-210084.
- Saxena, A., Tripathi, A., Talukdar, P. (2020). Improving Multi-hop Question Answering over Knowledge Graphs using Knowledge Base Embeddings. ACL 2020, pp. 4498–4507. DOI 10.18653/v1/2020.acl-main.412.(EmbedKGQA)
- Schick, T., Dwivedi-Yu, J., Dessì, R., Raileanu, R., Lomeli, M., Hambro, E., Zettlemoyer, L., Cancedda, N., Scialom, T. (2023). Toolformer: Language Models Can Teach Themselves to Use Tools. NeurIPS 2023. arXiv:2302.04761.(著者リストは camera-ready 版9名に従う)
- Sinha, K., Sodhani, S., Dong, J., Pineau, J., Hamilton, W. L. (2019). CLUTRR: A Diagnostic Benchmark for Inductive Reasoning from Text. EMNLP-IJCNLP 2019, pp. 4506–4515. arXiv:1908.06177.
- Sun, J., Xu, C., Tang, L., Wang, S., Lin, C., Gong, Y., Ni, L. M., Shum, H.-Y., Guo, J. (2024). Think-on-Graph: Deep and Responsible Reasoning of Large Language Model on Knowledge Graph. ICLR 2024. arXiv:2307.07697.
- Szeider, S. (2025). MCP-Solver: Integrating Language Models with Constraint Programming Systems. arXiv:2501.00539 [preprint].
- Tafjord, O., Dalvi, B., Clark, P. (2021). ProofWriter: Generating Implications, Proofs, and Abductive Statements over Natural Language. Findings of ACL-IJCNLP 2021, pp. 3621–3634. DOI 10.18653/v1/2021.findings-acl.317.
- Thorne, J., Vlachos, A., Christodoulopoulos, C., Mittal, A. (2018). FEVER: a Large-scale Dataset for Fact Extraction and VERification. NAACL-HLT 2018, pp. 809–819. DOI 10.18653/v1/N18-1074.
- Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E., Narang, S., Chowdhery, A., Zhou, D. (2023). Self-Consistency Improves Chain of Thought Reasoning in Language Models. ICLR 2023. arXiv:2203.11171.
- Xu, W., Liang, Z., Mei, K., Gao, H., Tan, J., Zhang, Y. (2025). A-MEM: Agentic Memory for LLM Agents. NeurIPS 2025. arXiv:2502.12110.
- Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., Cao, Y. (2023). ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023. arXiv:2210.03629.
- Ye, X., Chen, Q., Dillig, I., Durrett, G. (2023). SatLM: Satisfiability-Aided Language Models Using Declarative Prompting. NeurIPS 2023. arXiv:2305.09656.
- Zhang, Y., Dai, H., Kozareva, Z., Smola, A., Song, L. (2018). Variational Reasoning for Question Answering with Knowledge Graph. AAAI 2018. arXiv:1709.04071. DOI 10.1609/aaai.v32i1.12057.(MetaQA — データセット名は論文本文でなく公式リポジトリ由来)

---

## Appendix(予定)

- A. 合成 KB 生成器の詳細(スキーマ、注入アルゴリズム、gold ラベル検証)
- B. プロンプト全文(B0/B1/C1/C2、強化版 B1 を含む)と構造化抽出の仕様
- C. ProofWriter ルール写像の規則と除外項目一覧
- D. 公開ベンチマーク全項目版の結果表(uncontaminated 主表との対比)
- E. コスト・レイテンシの全計測値
- F. 誤り分析の分類基準と代表例
