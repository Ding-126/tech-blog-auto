#!/usr/bin/env bash
# 一眼看清：草稿 / 待发布 / 已发布 / 已发 URL 去重
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/blog_queue.py" status
