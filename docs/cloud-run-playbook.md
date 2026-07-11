# クラウド実行プレイブック(2026-07-11)

docs/design-revision-plan.md の実験続行計画をクラウドセッション
(claude.ai/code 等、GitHub からクローンする使い捨て環境)で走らせるための手順書。
1 セッション = 1 レーン。各レーンは独立に走らせられる。

## 0. セットアップ(全レーン共通、セッション冒頭で必ず)

```sh
bash scripts/cloud_setup.sh
```

- 実験データ(81MB のうち必要な ~18MB)は orphan ブランチ **`data-snapshot`**
  からバイト同一で展開される。ローカル再生成(seed=42)はフォールバック手段
  であり、既存結果との比較にはスナップショット版を使うこと。
- data/ は **untracked のまま** にする(`git add data/` 禁止 —
  .gitignore の `kb.ttl` ルールが一部ファイルだけ落とす)。

## 1. スモークテスト(全レーン共通、本走行前に必ず)

```sh
uv run python -m experiments.harness --dataset data/s100 --task-type claims \
  --condition B2 --limit 2 --out results/smoke-cloud.jsonl
```

合格基準: 2 レコードとも `cost_usd > 0` かつ `seconds > 5` かつ `tool_calls` が
非空。**`cost_usd: 0`・`seconds < 5`・ツールなし** はクォータ枯渇/認証死の署名
(過去に 648 セッションがこれで死亡)— その場合は本走行を始めず中断・報告。
スモーク結果はコミットしない(rm してよい)。

## 2. 共通規律

- **同時に走らせる重いストリームは全体で ≤3**(2026-07-08 のクォータ枯渇の教訓)。
- **結果は逐次コミット&プッシュ**: クラウド環境は使い捨て。レーンごとに
  ブランチ `run/<レーン名>-<日付>` を切り、**1 条件が終わるたびに**
  `git add results/<file> && git commit && git push`。worktree に置き忘れて
  73 レコードを失いかけた事故が既にある。
- 同じ `--out` ファイルに 2 レーンから書かない(追記モードのため混線する)。
- 集計時のキーは `correct` と `seconds`(`em` キーは存在しない)。
- 途中死したタスクは resume サブセット方式(欠損 task_id で qa.jsonl を
  フィルタした data/resume-* を作って再走、同じ --out に追記)。
- ハング系は修正済み(Z3 30s / SPARQL 30s / closure 120s / バッファ 10MB)だが、
  1 タスク 6 分超が続いたらプロセスを確認する。

### 並列化(シャーディング)— 壁時計時間の短縮

harness はタスクを直列に流すため 600 タスク ≈ 4 時間超かかる。`--shard K/N` で
1 条件を N プロセスに分割できる(タスク列の厳密な分割なので、連結結果は直列走行と
同値。クォータ消費は不変で、**壁時計時間だけが 1/N になる**):

```sh
scripts/run_sharded.sh 3 results/metaqa-pert-B2.jsonl -- \
  --dataset data/metaqa-perturbed --task-type qa --condition B2 --model sonnet
```

全シャード成功時のみ結果を連結する。一部失敗時は `-shardK.jsonl` が残るので、
失敗シャードだけ `--shard K/N` で再走してから手動で cat する。

**規律**: 同時実行数の上限(≤3 ストリーム)は**シャード数で数える** —
シャード 3 本 = 3 ストリームであり、その間ほかのレーンは走らせない。
クォータ枯渇の署名(cost 0・<5s・ツールなし)が 1 シャードでも出たら
全シャードを止めること。プロセス内の並行化(asyncio 同時実行)は
SIGALRM デッドラインと KB スクラッチコピーがプロセス内で共有されるため
**やらない**(プロセス分割が唯一の安全な並列化)。

## 3. レーン定義

### レーン A: claims への B2(コード変更ゼロ、~数$)

RQ1 防御。推論必須の claims で grep ベースの B2 が原理的に落ちることの実証。

```sh
for d in s100 s500 s2000 s5000; do
  uv run python -m experiments.harness --dataset data/$d --task-type claims \
    --condition B2 --out results/$d-B2.jsonl
done
```

命名は既存の `s{size}-{条件}.jsonl` に合わせる。約 192–300 claims × 4 グリッド。

### レーン B: 摂動版 B2(コード変更ゼロ、~$22)

頑健性の列の完成。600 QA。

```sh
uv run python -m experiments.harness --dataset data/metaqa-perturbed \
  --task-type qa --condition B2 --out results/metaqa-pert-B2.jsonl
```

### レーン C: 4a/4c の haiku 追試(コード変更ゼロ、~$10)

sonnet の天井を降ろして条件間分離を出す。

```sh
# 4a: QuixBugs + bugsuite × plain/nsai
for c in plain nsai; do
  uv run python -m experiments.coding4a --condition $c --model haiku \
    --out results/coding4a-quixbugs-$c-haiku.jsonl
  uv run python -m experiments.coding4a --condition $c --model haiku \
    --suite bugsuite --out results/coding4a-bugsuite-$c-haiku.jsonl
done
# 4c: self / judge
for c in self judge; do
  uv run python -m experiments.coding4c --suite bugsuite --condition $c \
    --model haiku --out results/coding4c-$c-haiku.jsonl
done
```

### レーン D: H1 委譲ハンドオフ構造化 + C2f null 51 件再走(実装小、~$4)

**論文の主張を反転させ得る最優先レーン。** 仕様は
docs/design-revision-plan.md §H1。実装 2 箇所 + prompt_rev バンプ:

1. `src/nsai/subagents.py` の `EXPLORER_PROMPT`(40 行目付近)—
   報告の末尾に `FINAL: ns:<answer>`(+根拠トリプル列)を必須化
2. `experiments/harness.py` の `QA_DELEGATE`(111 行目付近)—
   「サブエージェントの FINAL 行を逐語コピーして自分の FINAL とする」を明記
3. `experiments/harness.py` の `PROMPT_REV`(136 行目)を 2 → 3 に

```sh
uv run python -m experiments.harness --dataset data/resume-C2f-null \
  --task-type qa --condition C2f --out results/metaqa-C2f-rev3-null51.jsonl
```

**注意: 既存の results/metaqa-C2f.jsonl に追記しない**(prompt_rev が違うため
別ファイル)。判定: 51 件中 null が 0 になり正答が積めば、C2f 全体の実力は
rev2 の正答 462 + 今回の正答で再計算できる(0.86 前後の見込み)。
実装はテスト(`uv run pytest tests -x -q`)を通してから走らせる。

### レーン E: S3 kb_violations + audit 再走(実装中、~$10–30)

仕様は docs/design-revision-plan.md §S3。Precision 0.05→1.00 の実測が狙い。

1. `src/nsai/kb.py` に閉包前の functional-property 違反列挙
   (provenance 同梱・一括返却)を実装、`src/nsai/tools.py` で
   `kb_violations` としてツール化、`ALL_TOOLS` と
   `src/nsai/subagents.py` の `AUDITOR_TOOLS` に追加
2. kb_infer 経由の閉包由来トリプルに asserted/inferred の区別を付ける
   (最低限、kb_infer の返り値に「閉包は矛盾 KB 上で sameAs 連鎖を生む」旨の
   警告と件数を含める)
3. `PROMPT_REV` バンプ(レーン D と衝突するなら先に D をマージ)

```sh
for g in a500c5 a500c20 a2000c5 a2000c20; do
  for c in C1 C2; do
    uv run python -m experiments.harness --dataset data/$g --task-type audit \
      --condition $c --runs 3 --out results/audit-$g-$c-rev3.jsonl
  done
done
```

B1 は再走不要(12/12 満点、変更の影響なし)。

### レーン F: S1 kb_check_answer + S4 verify-before-FINAL + 失敗タスク再走(実装中、~$10)

仕様は docs/design-revision-plan.md §S1/§S4。着手は D・E の後
(prompt_rev の系譜を単純に保つ)。失敗タスクサブセットは
results/metaqa-C1.jsonl の `correct: false` の task_id で data/metaqa/qa.jsonl
をフィルタして data/resume-C1-failed/ を作る(レーン D の
data/resume-C2f-null/ と同じ構造: qa.jsonl + kb.ttl + entities.json + meta.json)。
回復率だけでなく、正解していた対照群(同数を無作為抽出)の非退行も確認する。

## 4. 実行順序の推奨

| 波 | レーン | 理由 |
|---|---|---|
| 第1波(並行 ≤3) | A + B + D の実装 | A/B はゼロコード。D は実装が小さく効果最大 |
| 第2波 | D の再走 + C | D は 51 件 ~$4 ですぐ終わる |
| 第3波 | E → F | 実装レーン。prompt_rev を D→E→F の順に積む |

統計用の run 1–4(合成)/ run 1–2(MetaQA)は、**D–F の修正の扱いを決めてから**
別途計画する(修正前後の条件が混ざった run は統計にかけられない)。

## 5. 終了時(各レーン)

1. 集計を出して結果ブランチにコミット: 例
   `uv run python -m experiments.report`(または docs/results-summary.md の
   該当表を手で更新)
2. `git push` を確認してからセッションを終える(未プッシュの結果 = 消える)
3. PR を出すか、ローカルの main へのマージはユーザー判断に委ねる
