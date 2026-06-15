#!/usr/bin/env bash
# 36氪 RSS -> data/36kr-feed.json（按关键词过滤科技/AI）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/data/36kr-feed.json"

python3 - "$OUT" <<'PY'
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

out_path = sys.argv[1]
keywords = re.compile(
    r"AI|人工智能|开源|Java|云|数据库|编程|开发者|大模型|Agent|钉钉|阿里云|腾讯",
    re.I,
)

req = urllib.request.Request(
    "https://www.36kr.com/feed",
    headers={"User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(req, timeout=20) as resp:
    root = ET.fromstring(resp.read())

items = []
for item in root.findall("./channel/item")[:30]:
    title = (item.findtext("title") or "").strip()
    if not keywords.search(title):
        continue
    link = (item.findtext("link") or "").strip()
    desc = (item.findtext("description") or "")[:500]
    items.append({
        "source": "36kr",
        "id": link.rsplit("/", 1)[-1].split("?")[0],
        "title": title,
        "url": link,
        "score": 100,
        "summary": desc,
    })

out_path = __import__("pathlib").Path(out_path)
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")
print(f"==> 36kr: {len(items)} items -> {out_path}")
PY
