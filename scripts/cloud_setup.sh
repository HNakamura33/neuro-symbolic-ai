#!/usr/bin/env bash
# クラウド(または新規クローン)環境で実験を走らせるためのブートストラップ。
# 実験データは orphan ブランチ origin/data-snapshot からバイト同一で取り出す
# (全データは seed=42 で決定論的に再生成可能だが、run 間の比較可能性を
#  保証するためスナップショットを正とする)。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "== 1/4 実験データ (origin/data-snapshot) =="
git fetch origin data-snapshot
git restore --source=origin/data-snapshot --worktree -- data/
# 注意: data/ は untracked のまま維持する。作業ブランチに git add しないこと
# (.gitignore の kb.ttl ルールで一部だけ落ちる事故のもと)。

echo "== 2/4 QuixBugs (レーン C の 4a のみ必要) =="
if [ ! -d data/raw/quixbugs ]; then
  git clone --depth 1 https://github.com/jkoppel/QuixBugs data/raw/quixbugs \
    || echo "WARN: QuixBugs の取得に失敗(4a 以外には不要)"
fi

echo "== 3/4 依存関係 =="
uv sync --dev

echo "== 4/4 整合性チェック =="
uv run python - <<'EOF'
from pathlib import Path
expect = {
    "data/metaqa/qa.jsonl": 600,
    "data/metaqa-perturbed/qa.jsonl": 600,
    "data/resume-C2f-null/qa.jsonl": 51,
    "data/s100/claims.jsonl": 192,
    "data/s5000/claims.jsonl": 300,
}
bad = False
for p, n in expect.items():
    got = sum(1 for _ in open(p))
    ok = got == n
    bad |= not ok
    print(("OK " if ok else "NG ") + f"{p}: {got} 行 (期待 {n})")
for p in ("data/metaqa/kb.ttl", "data/a500c5/kb-audit.ttl",
          "data/evalplus/killable-humaneval.json"):
    exists = Path(p).exists()
    bad |= not exists
    print(("OK " if exists else "NG ") + p)
raise SystemExit(1 if bad else 0)
EOF
uv run python -c "import rdflib, owlrl, z3; print('OK symbolic deps (rdflib/owlrl/z3)')"

echo
echo "セットアップ完了。実行前に必ずスモークテスト(docs/cloud-run-playbook.md §1)を通すこと:"
echo "  uv run python -m experiments.harness --dataset data/s100 --task-type claims \\"
echo "    --condition B2 --limit 2 --out results/smoke-cloud.jsonl"
