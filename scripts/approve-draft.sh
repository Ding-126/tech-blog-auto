#!/usr/bin/env bash
# 审核通过：content/drafts/ → content/queue/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FILE="${1:?用法: approve-draft.sh content/drafts/xxx.md}"
exec python3 "$ROOT/scripts/blog_queue.py" approve "$FILE"
