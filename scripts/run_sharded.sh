#!/usr/bin/env bash
# 1 条件の走行を N プロセスに分割して壁時計時間を 1/N にする。
# クォータ消費は不変(同じタスクを同じ回数走らせる)なので、
# 同時実行数の規律(docs/cloud-run-playbook.md §2)はシャード数で数えること。
#
# 使い方:
#   scripts/run_sharded.sh <N> <out.jsonl> -- <experiments.harness の引数 (--out/--shard 抜き)>
# 例:
#   scripts/run_sharded.sh 3 results/metaqa-pert-B2.jsonl -- \
#     --dataset data/metaqa-perturbed --task-type qa --condition B2 --model sonnet
#
# 全シャード成功時のみ <out.jsonl> に連結する(追記)。一部失敗時は
# <out>-shard*.jsonl を残して終了するので、失敗シャードだけ再走してから
# 手動で cat すればよい。
set -euo pipefail
if [ "$#" -lt 3 ]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -14
  exit 2
fi
N="$1"; OUT="$2"; shift 2
[ "${1:-}" = "--" ] && shift
BASE="${OUT%.jsonl}"

pids=()
for k in $(seq 0 $((N - 1))); do
  uv run python -m experiments.harness "$@" --shard "$k/$N" \
    --out "$BASE-shard$k.jsonl" &
  pids+=("$!")
  sleep 5  # 起動の集中(同時セッション確立)を避ける
done

fail=0
for p in "${pids[@]}"; do
  wait "$p" || fail=1
done

if [ "$fail" -ne 0 ]; then
  echo "NG: 一部のシャードが失敗。$BASE-shard*.jsonl を残したままマージせず終了。" >&2
  echo "    失敗シャードのみ --shard K/$N で再走し、全部揃ってから手動で cat すること。" >&2
  exit 1
fi

cat "$BASE"-shard*.jsonl >> "$OUT"
rm "$BASE"-shard*.jsonl
echo "OK: $(wc -l < "$OUT") 行 -> $OUT"
