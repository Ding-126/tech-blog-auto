#!/usr/bin/env bash
# 多渠道分发脚本：将博客文章转换为各平台格式
# 用法: ./scripts/distribute-post.sh content/posts/xxx.md [--dry-run]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILE="${1:?用法: distribute-post.sh content/posts/xxx.md [--dry-run]}"
DRY_RUN="${2:-}"

if [ ! -f "$FILE" ]; then
  echo "错误: 文件不存在: $FILE" >&2
  exit 1
fi

SLUG="$(basename "$FILE" .md)"
OUT_DIR="$ROOT/distribution"
mkdir -p "$OUT_DIR" "$OUT_DIR/wechat" "$OUT_DIR/zhihu" "$OUT_DIR/toutiao"

# ===== 用 Python 解析 front matter（更稳定）=====
PYTHON_OUTPUT=$(python3 -c "
import re, sys, json
text = open(sys.argv[1], encoding='utf-8').read()

title_m = re.search(r\"title\\s*=\\s*'(.*?)'\", text)
title = title_m.group(1) if title_m else ''

tags_m = re.search(r\"tags\\s*=\\s*\\[(.*?)\\]\", text)
tags = tags_m.group(1) if tags_m else ''

cat_m = re.search(r\"categories\\s*=\\s*\\['(.*?)'\\]\", text)
cat = cat_m.group(1) if cat_m else 'daily'

src_m = re.search(r\"source_url\\s*=\\s*'(.*?)'\", text)
src = src_m.group(1) if src_m else ''

draft_m = re.search(r\"draft\\s*=\\s*(true|false)\", text)
draft = draft_m.group(1) if draft_m else 'false'

print(json.dumps({'title': title, 'tags': tags, 'category': cat, 'source_url': src, 'draft': draft}, ensure_ascii=False))
" "$FILE" 2>&1)

# 用 Python JSON 输出安全地提取变量（避免 eval 对中文标题的解析问题）
TITLE=$(echo "$PYTHON_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['title'])")
TAGS=$(echo "$PYTHON_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['tags'])")
CATEGORY=$(echo "$PYTHON_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['category'])")
SOURCE_URL=$(echo "$PYTHON_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['source_url'])")
DRAFT=$(echo "$PYTHON_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['draft'])")

echo "文件: $SLUG"
echo "标题: $TITLE"
echo "分类: $CATEGORY"
echo "标签: $TAGS"
echo "来源: $SOURCE_URL"
echo "草稿: $DRAFT"

if [ "$DRAFT" = "true" ]; then
  echo "警告: 文章为草稿状态，跳过分发"
  exit 0
fi

# ===== 提取正文（去掉 front matter）=====
BODY=$(python3 -c "
import sys
text = open(sys.argv[1], encoding='utf-8').read()
parts = text.split('+++')
if len(parts) >= 3:
    print(parts[2].strip())
else:
    # Try YAML-style
    parts = text.split('---')
    if len(parts) >= 3:
        print(parts[2].strip())
    else:
        print(text.strip())
" "$FILE")

# ===== 1. 微信公众号格式 =====
echo "生成微信公众号版本..."

cat > "$OUT_DIR/wechat/$SLUG.md" << WECHAT
$(echo "$BODY")

---

**原文链接**: $SOURCE_URL
WECHAT

# 纯文本版本方便复制到公众号编辑器
python3 -c "
import sys, re
text = open(sys.argv[1], encoding='utf-8').read()
# 代码块标记保留，但去掉 \`\`\` 中的语言标记（微信不识别）
text = re.sub(r'\`\`\`\w*\n', '\`\`\`\n', text)
open(sys.argv[2], 'w', encoding='utf-8').write(text)
print('Plain text version ready')
" "$OUT_DIR/wechat/$SLUG.md" "$OUT_DIR/wechat/${SLUG}-plain.txt"

# ===== 2. 知乎格式 =====
echo "生成知乎版本..."
cat > "$OUT_DIR/zhihu/$SLUG.md" << ZHIHU
# $TITLE

$(echo "$BODY")

---

发布于：$(date '+%Y-%m-%d')

原文链接：$SOURCE_URL

|> 更多技术干货，欢迎关注公众号「后端实战笔记」

ZHIHU

# ===== 3. 头条格式 =====
echo "生成头条版本..."
# 头条不支持复杂表格，用 Python 处理
python3 -c "
import sys, re
text = open(sys.argv[1], encoding='utf-8').read()
# 去掉表格行（行首为 | 的行）
text = re.sub(r'^\|.*\|$\n?', '', text, flags=re.MULTILINE)
# 去掉表格分隔行（| --- | --- |）
text = re.sub(r'^\|[\s\-:]+\|.*\|$\n?', '', text, flags=re.MULTILINE)
open(sys.argv[2], 'w', encoding='utf-8').write(text)
" "$FILE" "$OUT_DIR/toutiao/$SLUG.md"

# ===== 输出统计 =====
echo ""
echo "分发文件已生成:"
ls -lh "$OUT_DIR/wechat/$SLUG.md" "$OUT_DIR/zhihu/$SLUG.md" "$OUT_DIR/toutiao/$SLUG.md"
echo ""
echo "发布检查清单:"
echo "  [ ] 公众号: 复制 $OUT_DIR/wechat/$SLUG.md 到公众号编辑器"
echo "  [ ] 知乎:    将 $OUT_DIR/zhihu/$SLUG.md 粘贴到知乎"
echo "  [ ] 头条:    将 $OUT_DIR/toutiao/$SLUG.md 粘贴到头条"

if [ -n "$DRY_RUN" ]; then
  echo ""
  echo "DRY RUN 模式（仅生成文件，未发布）"
  echo "确认无误后，去掉 --dry-run 参数执行"
fi

echo ""
echo "分发完成: $(date '+%Y-%m-%d %H:%M:%S')"