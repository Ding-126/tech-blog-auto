#!/usr/bin/env bash
# 发布文章：更新去重清单、commit 并 push
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILE="${1:?用法: publish-post.sh content/posts/xxx.md}"

if [ ! -f "$FILE" ]; then
  echo "错误: 文件不存在: $FILE" >&2
  exit 1
fi

PUBLISHED="$ROOT/data/published-urls.json"
mkdir -p "$(dirname "$PUBLISHED")"
[ -f "$PUBLISHED" ] || echo '[]' > "$PUBLISHED"

# 从 front matter 提取 source_url，追加到去重清单
python3 - "$FILE" "$PUBLISHED" <<'PY'
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

post = Path(sys.argv[1])
published = Path(sys.argv[2])
text = post.read_text(encoding="utf-8")

source_url = ""
m = re.search(r"source_url\s*=\s*['\"]([^'\"]+)['\"]", text)
if m:
    source_url = m.group(1).strip()

title = ""
m = re.search(r"title\s*=\s*['\"]([^'\"]+)['\"]", text)
if m:
    title = m.group(1).strip()

records = json.loads(published.read_text(encoding="utf-8") or "[]")
urls = {r.get("url") for r in records if r.get("url")}

if source_url and source_url not in urls:
    records.append({
        "url": source_url,
        "title": title,
        "post": str(post),
        "published_at": datetime.now(timezone.utc).astimezone().isoformat(),
    })
    published.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded source_url: {source_url}")
elif source_url:
    print(f"source_url already published: {source_url}")
else:
    print("WARN: no source_url in front matter, skip dedup record")
PY

git add "$FILE" "$PUBLISHED"
if git diff --cached --quiet; then
  echo "Nothing to commit."
  exit 0
fi

SLUG="$(basename "$FILE" .md)"
git commit -m "post: ${SLUG} [auto]"
git push origin main
python3 "$ROOT/scripts/blog_queue.py" register-published "$FILE" 2>/dev/null || true
echo "Published: $FILE"
