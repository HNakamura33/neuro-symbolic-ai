#!/bin/bash
# Referee-response experiment runs (M1/M3/M5) — launch one wave at a time.
# Each stream is nohup-detached so it survives the launching session
# (lesson: session-spawned background harness runs die with the session).
# Quota rule: never more than 3 heavy streams at once.
#
# Usage: bash scripts/referee_runs.sh wave1|wave2|wave3
set -euo pipefail
REPO=/Users/hirotaka/neuro-symbolic-ai
REV5=$REPO/.claude/worktrees/rev5-pin
LOGS=$REPO/results/logs
mkdir -p "$LOGS"

launch() { # launch <dir> <log-name> <harness args...>
  local dir=$1 name=$2; shift 2
  (cd "$dir" && nohup uv run python -m experiments.harness "$@" \
      > "$LOGS/$name.log" 2>&1 & echo "$name pid $!")
}

case "${1:?wave1|wave2|wave3}" in
wave1)
  # M1: held-out rev5 (in the PROMPT_REV=5 pinned worktree) + held-out B2
  # M3: B2g (neural grep-gate) on the original 600
  launch "$REV5" metaqa-heldout-C1-rev5 \
    --dataset data/metaqa-heldout --task-type qa --condition C1 --model sonnet \
    --out "$REPO/results/metaqa-heldout-C1-rev5.jsonl"
  launch "$REPO" metaqa-heldout-B2 \
    --dataset data/metaqa-heldout --task-type qa --condition B2 --model sonnet \
    --out "$REPO/results/metaqa-heldout-B2.jsonl"
  launch "$REPO" metaqa-B2g \
    --dataset data/metaqa --task-type qa --condition B2g --model sonnet \
    --out "$REPO/results/metaqa-B2g.jsonl"
  ;;
wave2)
  # M5: second runs of the two headline conditions; M1: perturbed held-out rev5
  # rev5 runs MUST launch from $REV5 (PROMPT_REV=5); main is at rev 6.
  launch "$REV5" metaqa-C1-rev5-run1 \
    --dataset data/metaqa --task-type qa --condition C1 --model sonnet \
    --out "$REPO/results/metaqa-C1-rev5-run1.jsonl"
  launch "$REPO" metaqa-B2-run1 \
    --dataset data/metaqa --task-type qa --condition B2 --model sonnet \
    --out "$REPO/results/metaqa-B2-run1.jsonl"
  launch "$REV5" metaqa-heldout-pert-C1-rev5 \
    --dataset data/metaqa-heldout-pert --task-type qa --condition C1 --model sonnet \
    --out "$REPO/results/metaqa-heldout-pert-C1-rev5.jsonl"
  ;;
wave3)
  # M5: third runs (rev5 from the pinned worktree, see wave2 note)
  launch "$REV5" metaqa-C1-rev5-run2 \
    --dataset data/metaqa --task-type qa --condition C1 --model sonnet \
    --out "$REPO/results/metaqa-C1-rev5-run2.jsonl"
  launch "$REPO" metaqa-B2-run2 \
    --dataset data/metaqa --task-type qa --condition B2 --model sonnet \
    --out "$REPO/results/metaqa-B2-run2.jsonl"
  ;;
esac
