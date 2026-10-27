#!/bin/sh
# Optional git pre-push hook: copy to .git/hooks/pre-push and make it executable.
# Refuses a push when the latest review is missing, stale, incomplete, or on HOLD.
# Override once with: HUNK_LEDGER_SKIP=1 git push
[ "$HUNK_LEDGER_SKIP" = "1" ] && exit 0
L=.hunk-ledger
SCRIPT=${HUNK_LEDGER_SCRIPT:-scripts/hunkledger.py}
if [ ! -f "$L/ledger.json" ] || [ ! -f "$L/review.json" ]; then
  echo "hunk-ledger: no review found in $L/. Review the change first." >&2
  exit 1
fi
python3 "$SCRIPT" receipt "$L/ledger.json" "$L/review.json" --block "${HUNK_LEDGER_BLOCK:-major}" --recheck
code=$?
[ $code -eq 0 ] && exit 0
echo "hunk-ledger: push refused (exit $code)." >&2
exit 1
