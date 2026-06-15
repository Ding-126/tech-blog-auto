#!/usr/bin/env bash
# 从待发布队列取最早 1 篇 → content/posts/，再 git push
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

POST="$(python3 "$ROOT/scripts/blog_queue.py" publish-next --quiet)"

if [ -z "${POST:-}" ]; then
  echo "没有可 push 的文章。"
  exit 0
fi

"$ROOT/scripts/publish-post.sh" "$POST"
