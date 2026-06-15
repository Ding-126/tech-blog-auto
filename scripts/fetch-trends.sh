#!/usr/bin/env bash
# 抓取 HN +（可选）GitHub + 掘金 + 36氪，写入 data/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/data"
mkdir -p "$OUT_DIR"

# 可选：从 blog-repo/.env 加载 GITHUB_TOKEN（勿提交 git）
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT/.env"
  set +a
fi

# macOS / Linux 兼容：昨天日期
if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
  YESTERDAY=$(date -v-1d +%Y-%m-%d)
else
  YESTERDAY=$(date -d 'yesterday' +%Y-%m-%d)
fi

echo "==> Fetching Hacker News top stories..."
curl -fsS "https://hacker-news.firebaseio.com/v0/topstories.json" \
  | python3 -c "
import json, sys, urllib.request

ids = json.load(sys.stdin)[:20]
items = []
for i in ids:
    with urllib.request.urlopen(
        f'https://hacker-news.firebaseio.com/v0/item/{i}.json',
        timeout=15,
    ) as resp:
        d = json.load(resp)
    if d.get('url'):
        items.append({
            'source': 'hn',
            'id': d.get('id'),
            'title': d.get('title', ''),
            'url': d.get('url', ''),
            'score': d.get('score', 0),
            'summary': (d.get('text') or '')[:500],
        })
print(json.dumps(items, ensure_ascii=False, indent=2))
" > "$OUT_DIR/hn-top.json"

echo "==> HN: $(python3 -c "import json; print(len(json.load(open('$OUT_DIR/hn-top.json'))))") items -> $OUT_DIR/hn-top.json"

if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "==> Fetching GitHub trending (search API)..."
  curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/search/repositories?q=created:>${YESTERDAY}&sort=stars&order=desc&per_page=15" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = []
for r in data.get('items', []):
    items.append({
        'source': 'github',
        'title': r.get('full_name', ''),
        'url': r.get('html_url', ''),
        'score': r.get('stargazers_count', 0),
        'summary': (r.get('description') or '')[:500],
    })
print(json.dumps(items, ensure_ascii=False, indent=2))
" > "$OUT_DIR/gh-trending.json"
  echo "==> GitHub: $(python3 -c "import json; print(len(json.load(open('$OUT_DIR/gh-trending.json'))))") items -> $OUT_DIR/gh-trending.json"
else
  echo "==> Skip GitHub trending (set GITHUB_TOKEN to enable)"
fi

echo "==> Fetching Juejin hot lists..."
"$ROOT/scripts/fetch-juejin.sh"

if [ -x "$ROOT/scripts/fetch-36kr.sh" ]; then
  echo "==> Fetching 36kr RSS..."
  "$ROOT/scripts/fetch-36kr.sh"
else
  echo "==> Skip 36kr (scripts/fetch-36kr.sh not found)"
fi

# 合并为一份供 Hermes 选题
python3 -c "
import json, pathlib
root = pathlib.Path('$OUT_DIR')
merged = []
for name in ('hn-top.json', 'gh-trending.json', 'juejin-hot.json', '36kr-feed.json'):
    p = root / name
    if p.exists():
        merged.extend(json.loads(p.read_text()))
SOURCE_BOOST = {
    'juejin': 1.2,
    '36kr': 1.0,
    'hn': 1.0,
    'github': 0.8,
}
for item in merged:
    src = item.get('source', '')
    boost = SOURCE_BOOST.get(src, 1.0)
    item['score'] = int(item.get('score', 0) * boost)
merged.sort(key=lambda x: x.get('score', 0), reverse=True)
(root / 'trends-merged.json').write_text(
    json.dumps(merged, ensure_ascii=False, indent=2) + '\n'
)
print(len(merged))
" | {
  read -r count
  echo "==> Merged $count items -> $OUT_DIR/trends-merged.json"
}

echo "Done."
