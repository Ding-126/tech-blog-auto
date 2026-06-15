#!/usr/bin/env bash
# 将未发布的文章排版并发布到公众号草稿箱
# 建议配合 cron 每天 08:00 运行
# 使用: bash scripts/publish-new.sh

set -euo pipefail
cd "$(dirname "$0")/.."

TOOL_DIR="$HOME/.hermes/skills/xiaohu-wechat-format"
TRACKING_FILE="data/wechat-published.json"

# 确保 tracking 文件存在
[ -f "$TRACKING_FILE" ] || echo "[]" > "$TRACKING_FILE"

for file in content/posts/*.md; do
  slug=$(basename "$file" .md)
  [ "$slug" = "hello-world" ] && continue

  # 跳过已发布的
  if python3 -c "import json; d=json.load(open('$TRACKING_FILE')); exit(0 if '$slug' in d else 1)" 2>/dev/null; then
    echo "⏭️ $slug 已发布"
    continue
  fi

  echo "📝 $slug"
  # 排版
  bash "$(dirname "$0")/../自动化推文流水线/wechat-format.sh" "$slug"
  
  # 发布
  python3 scripts/ci-wechat-publish.py "$file" 2>&1 | tail -5

  # 标记为已发布
  python3 -c "
import json
d = json.load(open('$TRACKING_FILE'))
d.append('$slug')
with open('$TRACKING_FILE','w') as f: json.dump(d,f,indent=2)
"
  echo "✅ $slug 发布完成"
done
