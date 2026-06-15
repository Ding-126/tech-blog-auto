#!/usr/bin/env bash
# 掘金后端 + AI 热榜 -> data/juejin-hot.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/data"
mkdir -p "$OUT_DIR"

python3 - "$OUT_DIR/juejin-hot.json" <<'PY'
import json
import pathlib
import sys
import urllib.request

out = pathlib.Path(sys.argv[1])
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
CATEGORIES = ["6809637769959178254", "6809637773935378440"]

def fetch_rank(category_id: str):
    url = (
        "https://api.juejin.cn/content_api/v1/content/article_rank"
        f"?category_id={category_id}&type=hot&spider=0"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    if data.get("err_no") != 0:
        raise RuntimeError(data)
    return data.get("data") or []

items, seen = [], set()
for cat_id in CATEGORIES:
    for row in fetch_rank(cat_id):
        content = row.get("content") or {}
        cid = content.get("content_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        counter = row.get("content_counter") or {}
        items.append({
            "source": "juejin",
            "id": cid,
            "title": content.get("title") or "",
            "url": f"https://juejin.cn/post/{cid}",
            "score": int(counter.get("view") or 0),
            "summary": (content.get("brief") or "")[:500],
        })

items.sort(key=lambda x: x["score"], reverse=True)
out.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")
print(len(items))
PY

echo "==> Juejin: $(python3 -c "import json; print(len(json.load(open('$OUT_DIR/juejin-hot.json'))))") items -> $OUT_DIR/juejin-hot.json"
