# nsai — Neuro-Symbolic AI Agent CLI

Claude (ニューラル) と RDF 知識グラフ + OWL-RL 推論 + 制約/SMT ソルバー (シンボリック) を組み合わせたエージェント CLI。

LLM は自然言語の理解と定式化だけを担い、**事実の保存・推論・検証・求解はすべて記号層が行う**。記号層が確認するまで、エージェントは事実を断定しない。

## 主な機能

- **知識ベース構築** — 会話や文書から事実をトリプルとして抽出し、Turtle ファイルに永続化(出典ログ付き)
- **推論付き質問応答** — SPARQL + RDFS/OWL-RL 推論で厳密に回答
- **ハルシネーション検証** — 主張をトリプルに分解し、KB との含意 (`entailed`) / 矛盾 (`contradicted`) / 不明 (`unknown`) を判定
- **プランニング** — 制約充足問題を定式化してソルバーで厳密解を列挙
- **数理検証** — Z3 で数値・論理の主張を証明、または反例を生成
- **ハイブリッドコーディング** — 通常のコーディング(ファイル操作)にシンボリック検証を組み合わせ、境界条件・入力検証を Z3 で証明/反証

## アーキテクチャ

```mermaid
flowchart TB
    User([ユーザー]) <--> CLI["nsai CLI (typer)<br/>chat / ask / verify / ingest / code / kb *"]

    CLI <--> Agent["エージェントループ<br/>Claude Agent SDK — ClaudeSDKClient"]
    CLI -- "kb * サブコマンド<br/>(LLMなしの直接操作)" --> KB

    subgraph Neural["ニューラル層"]
        Agent <--> Claude["Claude<br/>自然言語理解・トリプル/制約への定式化"]
    end

    Agent <--> MCP["in-process MCP server『symbolic』<br/>permission_mode=dontAsk<br/>(通常モード: ファイル・bash・ネットワークは遮断)"]

    subgraph Symbolic["シンボリック層"]
        MCP --> T1["kb_add_triples<br/>kb_find / kb_sparql<br/>kb_verify / kb_infer<br/>kb_stats / kb_provenance"]
        MCP --> T2["csp_solve"]
        MCP --> T3["smt_verify"]

        T1 --> KB["kb.py<br/>rdflib Graph + owlrl<br/>(OWL-RL 閉包)"]
        T2 --> CSP["csp.py<br/>python-constraint<br/>(有限領域 CSP)"]
        T3 --> SMT["smt.py<br/>Z3<br/>(証明 / 反例)"]
    end

    KB --> TTL[("kb.ttl<br/>Turtle 永続化")]
    KB --> PROV[("kb.prov.jsonl<br/>出典ログ")]
```

### 検証フロー(ハルシネーション検出)

```mermaid
sequenceDiagram
    participant U as ユーザー
    participant C as Claude (ニューラル)
    participant V as kb_verify (シンボリック)
    participant G as 知識グラフ + OWL-RL

    U->>C: 「アリスは大阪で生まれた」を検証して
    C->>C: 原子的トリプルに分解<br/>(ns:alice, ns:born_in, ns:osaka)
    C->>V: kb_verify(alice, born_in, osaka)
    V->>G: OWL-RL 閉包を計算して照合
    Note over G: born_in は owl:FunctionalProperty<br/>KB は born_in=tokyo を含意
    G-->>V: contradicted
    V-->>C: verdict + 根拠
    C-->>U: ❌ 矛盾 — KBによればアリスは東京生まれ
```

## セットアップ

```sh
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
```

## 使い方

### エージェント経由(LLM)

```sh
uv run nsai chat                          # 対話モード(マルチターン)
uv run nsai ask "ソクラテスは死ぬ?"         # ワンショット質問
uv run nsai verify "アリスは東京生まれ"      # 主張の検証
uv run nsai ingest document.txt           # 文書から KB 構築
uv run nsai ask -m haiku "..."            # モデル指定(コスト節約)
```

### ハイブリッドコーディングモード

```sh
uv run nsai code                          # 対話コーディングセッション
uv run nsai code "pager.py の page_count を境界バグ監査して"   # ワンショット
```

コーディングはニューラル層(ファイル操作ツール)が行い、危ういロジックの検証だけシンボリック層に委ねるモード。

```mermaid
flowchart TB
    User([ユーザー]) <--> Agent["nsai code — エージェントループ<br/>(Claude: 理解・設計・実装・定式化)"]

    subgraph Neural["ニューラル層 — コーディング作業"]
        Agent <--> FT["Read / Glob / Grep / Edit / Write<br/>(自動許可)"]
        FT <--> Code[("コードベース")]
        Agent -. "コマンドごと" .-> Gate{"y/N 確認<br/>can_use_tool"}
        Gate -- 承認 --> Bash["Bash<br/>(テスト実行など)"]
        Gate -- 拒否 --> Agent
    end

    subgraph Symbolic["シンボリック層 — 決定論的検証チェックポイント"]
        SMT["smt_verify (Z3)<br/>① 入力検証: バリデーション突破値の探索<br/>② 出力検証: 実装後の主張を証明<br/>③ 境界・オフバイワン監査"]
        CSP["csp_solve<br/>④ 設定・割当・順序の厳密解"]
        KBV["kb_verify / kb_add_triples<br/>⑤ プロジェクト記憶との矛盾検出"]
    end

    Agent -- "主張を定式化" --> SMT
    SMT -- "proved / 反例(→回帰テスト化)" --> Agent
    Agent -- "制約を定式化" --> CSP
    CSP -- "全解 / 解なし" --> Agent
    Agent -- "契約・インバリアントを記録/照合" --> KBV
    KBV -- "entailed / contradicted" --> Agent
```

シンボリック層の使いどころ:

- **入力検証** — バリデーションを通過しても事前条件を破れる値がないか `smt_verify` で探索。反例 = 具体的な攻撃/エッジ入力 → ガード追加 + 回帰テスト化
- **出力検証** — 実装後に「このガード下では返り値は必ず [0, len-1] 内」等の主張を Z3 で証明。反証されたらコードを修正し、反例をテストケースに変換
- **境界・オフバイワン監査** — ループ範囲・ページネーション・日付演算を整数として符号化し、目視でなく証明で確認
- **プロジェクト記憶** — 証明済みインバリアントや API 契約を KB に蓄積(出典付き)。コードが変わって記録と矛盾すれば `contradicted` で検出

権限設計: ファイルの読み書き(Read/Glob/Grep/Edit/Write)は自動許可、**bash はコマンドごとに y/n 確認**、それ以外(ネットワーク等)は拒否。

### KB 直接操作(LLMなし)

```sh
uv run nsai kb add ns:socrates rdf:type ns:Human --source "user"
uv run nsai kb add ns:Human rdfs:subClassOf ns:Mortal
uv run nsai kb check ns:socrates rdf:type ns:Mortal   # → entailed
uv run nsai kb source ns:socrates rdf:type ns:Human   # 出典を表示
uv run nsai kb show
uv run nsai kb query "SELECT ?s WHERE { ?s a ns:Human }"
uv run nsai kb infer                                  # 推論結果を実体化
uv run nsai kb export backup.ttl                      # / kb import backup.ttl
uv run nsai kb build-from-code src/                   # ast で構造的事実を抽出(LLM不要)
uv run nsai kb stats
```

KB ファイルは既定で `./kb.ttl`(`--kb` オプション or `NSAI_KB` 環境変数で変更可)。

## エージェントのツール(9種)

| ツール | 役割 |
|---|---|
| `kb_add_triples` | 事実をトリプルとして保存(source 付き) |
| `kb_find` | パターンマッチ(ワイルドカード可) |
| `kb_sparql` | SPARQL SELECT / ASK |
| `kb_verify` | OWL-RL 推論込みで主張を判定(entailed / contradicted / unknown) |
| `kb_infer` | RDFS/OWL-RL 閉包を KB に実体化 |
| `kb_stats` | KB サイズ統計 |
| `kb_provenance` | トリプルの出典を照会 |
| `csp_solve` | 有限領域の制約充足(スケジューリング等) |
| `smt_verify` | Z3 で数値・論理主張を証明 / 反証 |

セキュリティ: 通常モードでは `permission_mode="dontAsk"` + `allowed_tools` により、エージェントは上記9ツール**のみ**使用可能(ファイルシステム・bash・ネットワークは遮断)。`nsai code` のみファイルツールを追加で許可し、bash は都度確認。

## 開発

```sh
uv run pytest        # テスト(26件)
```

## ロードマップ

- [サブエージェントへの決定論的推論の委譲](docs/future-extensions.md) — symbolic-explorer(知識グラフの agentic search)/ kb-auditor(矛盾の系統的走査)/ test-generator(反例・境界値からのテスト生成)/ loop-judge(ループ終了条件の形式化と判定)
- [静的解析による KB 構築(code2kb)](docs/future-extensions.md#拡張2-静的解析による-kb-構築code2kb) — `ast` による決定論的な構造抽出(import/呼び出し/継承)+ LLM による意味層(契約・意図)の分業

## 矛盾検出のモデリングのコツ

1つの主語に対して1つの値しか取れないプロパティ(出生地・首都など)は
`owl:FunctionalProperty` として宣言すると、異なる値の主張が `contradicted` として検出可能になる:

```sh
uv run nsai kb add ns:born_in rdf:type owl:FunctionalProperty
uv run nsai kb add ns:alice ns:born_in ns:tokyo
uv run nsai kb check ns:alice ns:born_in ns:osaka   # → contradicted
```
