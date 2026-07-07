# 将来の拡張

ステータス: 拡張1・拡張2ともに実装済み / 起案: 2026-07-07 / 実装完了: 2026-07-07

- [拡張1: サブエージェントへの決定論的推論の委譲](#拡張1-サブエージェントへの決定論的推論の委譲)
- [拡張2: 静的解析による KB 構築(code2kb)](#拡張2-静的解析による-kb-構築code2kb)

---

# 拡張1: サブエージェントへの決定論的推論の委譲

> ✅ **実装済み** — `src/nsai/subagents.py` に5体すべて(symbolic-explorer /
> kb-auditor / prover / test-generator / loop-judge)を定義。`agent.py` の
> `build_options` が `agents=` と `Task` ツールを配線する。test-generator と
> loop-judge はファイル/Bash ツールを要するためコーディングモード限定。
> Bash は従来どおりコマンドごとの y/N 確認を通る。

## 概要

現在の nsai はメインエージェント1体がシンボリックツールを直接呼ぶ構成だが、
Claude Agent SDK はサブエージェントをネイティブにサポートしている
(`ClaudeAgentOptions(agents={...})` に `AgentDefinition` を渡すと、
メインエージェントが `Task` ツールで委譲できる)。

これを使い、**決定論的な推論基盤(KB / ソルバー)の上を歩き回る専門サブエージェント**
にツールコールの多い探索・監査タスクを委譲する。

```mermaid
flowchart TB
    User([ユーザー]) <--> Main["メインエージェント<br/>(強いモデル: 理解・設計・統合)"]

    Main -- "Task: 探索を委譲" --> Explorer["symbolic-explorer<br/>(haiku: マルチホップ探索)"]
    Main -- "Task: 監査を委譲" --> Auditor["kb-auditor<br/>(haiku: 矛盾の系統的走査)"]

    subgraph Symbolic["決定論的基盤(共有)"]
        KB["知識グラフ + OWL-RL"]
        SMT["Z3"]
    end

    Explorer <--> KB
    Auditor <--> KB
    Main <--> KB
    Main <--> SMT

    Explorer -- "蒸留された結論<br/>(パス + 根拠トリプル)" --> Main
    Auditor -- "矛盾レポート" --> Main
```

## 動機

1. **コンテキスト隔離** — マルチホップ探索(kb_find → 中間ノード発見 →
   kb_sparql → …)は数十回のツールコールを要する。サブエージェントに委譲すれば
   試行錯誤はメインの会話コンテキストを汚さず、蒸留された結論だけが戻る。
2. **「決定論的な地面の上を、ニューラルが歩く」** — 各ホップの結果は厳密
   (SPARQL)で、ニューラルな判断は経路選択のみ。ハルシネーションの余地が
   経路選択に限定され、返ってきたパスは全トリプルが KB で再検証可能。
   GraphRAG のエージェント版。
3. **コスト構造** — 1ホップの判断は単純なので探索は haiku で十分
   (`AgentDefinition(model="haiku")`)。メインは強いモデルのまま、
   ツールコールの嵩む部分だけ安いモデルに逃がせる。

## 提案するサブエージェント

### symbolic-explorer — オントロジー/知識グラフの agentic search

- **ツール**: kb_find / kb_sparql / kb_verify / kb_stats のみ(読み取り専用)
- **探索戦略**(システムプロンプトで規定):
  1. スキーマの把握 — どんな述語・クラスがあるか kb_sparql で調べる
  2. ホップ — 起点から有望な述語を選んで辿る。行き止まりならバックトラック
  3. 報告 — 発見したパスを根拠トリプル付きで返す(パス上の全トリプルが
     KB で検証可能であること)
- **ユースケース**:
  - 影響分析(コーディングモードと相性が良い): 「API X の仕様変更で壊れる
    モジュールは?」— KB に蓄積した依存関係・契約を推移的に辿り、各候補を
    kb_verify で確認
  - 根本原因分析: 障害事象から因果・依存エッジを遡る
  - マルチホップ QA: 中間エンティティを要する質問
    (「アリスの上司の出身地は?」)

### kb-auditor — 矛盾ハンティング

- **ツール**: kb_find / kb_sparql / kb_verify / kb_provenance
- **役割**: KB 全体を系統的に走査し、owl:FunctionalProperty 衝突や
  sameAs / differentFrom 矛盾を洗い出す。矛盾ごとに kb_provenance で
  出典を引き、どの情報源同士が食い違っているかまで報告する
- **ユースケース**: `ingest` で複数文書を取り込んだ後の整合性チェック、
  コーディングモードで蓄積した契約・インバリアントの定期監査

### test-generator — 反例・境界値からのテストケース生成

- **ツール**: smt_verify / csp_solve / Read(対象コードの読み取り)
- **役割**: 対象関数の事前条件・分岐条件を制約として符号化し、3系統の入力を
  系統的に生成する:
  1. **反例** — smt_verify で「バリデーションを通過するが事後条件を破る」値を
     探索。見つかればバグを露出する入力そのもの
  2. **境界値** — 各制約の等号成立点(`x <= 100` なら 100 と 101)をソルバーで
     具体化
  3. **同値クラス代表値** — 分岐条件で入力空間を分割し、各クラスの代表を
     csp_solve で列挙
- **出力**: pytest 形式のテストコード + 各ケースの根拠
  (どの制約のどの境界か)。目視のテスト設計と違い、境界の列挙に漏れがない
- **ユースケース**: コーディングモードで実装完了後に委譲し、
  「証明 + 生成テスト」をセットで納品する

### loop-judge — ループ終了条件の定義と判定(loop engineering)

- **役割**: エージェンティックループ(実装 → 検証 → 修正 → …)の終了条件を
  **機械判定可能な述語の合取**として形式化し、各イテレーション後に
  決定論的に判定する:

  ```text
  done := 全テスト成功 (Bash)
        ∧ 宣言した主張が全て proved (smt_verify)
        ∧ KB に contradicted な契約がない (kb_verify)
        ∧ イテレーション数が予算内
  ```

- **動機**: LLM の「もう大丈夫そう」という確率的判断でループを止めると、
  早すぎる終了(未検証のまま完了宣言)や無限ループ(基準が曖昧なまま
  修正を繰り返す)が起きる。終了判定を記号化すれば、ループの停止・継続の
  根拠が監査可能になる
- **設計**:
  - タスク開始時に終了条件を KB に記録
    (`ns:task_x ns:done_when ns:all_tests_pass` など)し、判定のたびに照合
  - **発散検出**も担当: 同一の反例・同一の失敗テストが2イテレーション
    連続したら「進捗停滞」として人間にエスカレーション
- **ユースケース**: コーディングモードの長い修正ループ、`ingest` 後の
  KB クレンジング(矛盾ゼロになるまで修正)の自動運転

### (候補)prover — SMT 補題分解

大きな主張を補題に分解し、各補題を smt_verify で証明して積み上げる。
探索よりも定式化の比重が大きいためモデルは強めが必要。優先度は上記2つより低い。

## 実装スケッチ

`agent.py` の `build_options` に追加(50行程度):

```python
from claude_agent_sdk import AgentDefinition

EXPLORER_TOOLS = [
    f"mcp__symbolic__{t}" for t in ("kb_find", "kb_sparql", "kb_verify", "kb_stats")
]

agents = {
    "symbolic-explorer": AgentDefinition(
        description="Multi-hop exploration of the knowledge graph. "
        "Use for questions requiring several hops or unknown paths.",
        prompt=EXPLORER_PROMPT,   # prompts.py に追加
        tools=EXPLORER_TOOLS,
        model="haiku",
    ),
    "kb-auditor": AgentDefinition(
        description="Systematic contradiction sweep over the KB.",
        prompt=AUDITOR_PROMPT,
        tools=AUDITOR_TOOLS,
        model="haiku",
    ),
}
# ClaudeAgentOptions(..., agents=agents) とし、allowed_tools に "Task" を追加
```

メインエージェントのシステムプロンプトには委譲基準を追記する:
「3ホップ以上かかりそうな探索、または KB 全体の走査が必要なタスクは
サブエージェントに委譲せよ」。

## 留保(実装を急がない理由)

- **SPARQL のプロパティパス(`ns:dep+` など)は1クエリで推移閉包を辿れる**。
  agentic search が効くのは「どの述語を辿るべきか事前に分からず、ホップごとに
  判断が要る」探索と、スキーマ自体を発見しながら進む場面に限られる
- 現状の KB は小規模で、探索の委譲は1回の SPARQL より割高になる。
  `ingest` の運用で数百〜数千トリプル規模になってから真価が出る
- サブエージェント呼び出しごとに新しいセッションが立つため、
  少数ツールコールで済むタスクではオーバーヘッドが逆転する

## 導入判断の目安

以下のいずれかを満たしたら実装する:

1. KB が概ね 500 トリプルを超え、マルチホップ質問でメインの応答品質が
   落ちはじめた
2. `ingest` を複数文書で常用しはじめ、取り込み後の整合性チェックが
   手動で回らなくなった
3. コーディングモードで蓄積した契約・依存関係に対して影響分析を
   問い合わせたくなった
4. コーディングモードの修正ループが長くなり、完了判定を目視で
   追えなくなった(→ loop-judge)

---

# 拡張2: 静的解析による KB 構築(code2kb)

## 現状の「会話や文書」の範囲

README の「会話や文書から事実をトリプルとして抽出」は、現時点では次を指す:

| ソース | 現状 | 抽出方法 |
|---|---|---|
| 会話 | ✅ 対応 | chat/code セッション中にユーザーが述べた事実を LLM が抽出(extract-then-store) |
| ドキュメント(設計書等) | ✅ 対応 | `nsai ingest <file>` — 任意のテキストを LLM が読んで抽出 |
| JSONL 会話履歴 | ✅ 対応 | `nsai ingest --jsonl` — ターンを認識・採番して整形し、ツールノイズを除去してから抽出(`src/nsai/history.py`) |
| ソースコード | ⚠️ 部分的 | テキストとして ingest すれば意図レベルの事実は抽出されるが、構造的事実の**網羅は保証されない**(LLM 抽出は確率的) |

## 提案: 静的解析との分業

構造的事実は LLM に抽出させる必要がない — Python の `ast` モジュールで
**決定論的・ゼロLLMコスト・網羅的**に抽出できる:

```text
(ns:module_a,  ns:imports,     ns:module_b)     # import グラフ
(ns:func_f,    ns:defined_in,  ns:module_a)     # 定義位置
(ns:func_f,    ns:calls,       ns:func_g)       # 呼び出しグラフ
(ns:ClassA,    rdfs:subClassOf, ns:ClassB)      # クラス階層
(ns:func_f,    ns:raises,      ns:ValueError)   # 例外
```

分業の構図:

- **静的解析(決定論的)** — 構造の「地面」を完全・正確に敷く:
  import / 呼び出し / 定義 / 継承 / シグネチャ
- **LLM 抽出(確率的)** — その上に意味の層を重ねる:
  契約(「None を返さない」)、インバリアント、設計判断、モジュールの意図
- **kb_verify** — 両層の**乖離を矛盾として検出**:
  記録された契約と実際の構造が食い違えば `contradicted`

拡張1の symbolic-explorer による影響分析は、静的解析による完全な依存グラフが
あって初めて信頼できる(LLM が偶々記録した範囲の探索では漏れる)。

## 実装スケッチ

```sh
nsai kb build-from-code src/          # ✅ 実装済み(src/nsai/code2kb/)
nsai ingest --jsonl history.jsonl     # ✅ 実装済み(src/nsai/history.py): 会話履歴をターン毎に整形して抽出
```

- `ast` visitor で上記述語のトリプルを抽出し、`source="<ファイルパス> (static-analysis)"`
  で provenance を記録。再実行時は差分のみ追加(add_triples が重複除去済み)
- コード変更後に再実行すれば、古い契約との矛盾が kb_verify で浮上する
- ✅ 多言語対応済み: Python (ast) / C/C++ / TypeScript / Rust (tree-sitter)。
  C/C++ は `--cpp-backend clang`(libclang, `nsai[clang]` extra)で高精度化できる

## 留保

- 呼び出しグラフは動的ディスパッチ(ダックタイピング、DI)を静的には
  解決できない — `ns:calls` は「静的に見える呼び出し」と明記して記録する
- KB が数千トリプル規模になるため、拡張1(explorer への委譲)の
  導入条件を同時に満たしやすい。拡張1と2はセットで効く
