#!/usr/bin/env bash
# ============================================================
# 全平台格式化脚本 — 一步生成 wechat/zhihu/toutiao 的 HTML
# 用法: ./scripts/format-all.sh content/posts/{slug}.md
# ============================================================
set -euo pipefail

FILE="${1:?用法: format-all.sh content/posts/xxx.md}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SLUG="$(basename "$FILE" .md)"
FORMATTER="/Users/dudu/workspace-daliy/xiaohu-wechat-format/scripts/format.py"
WECHAT_DIR="distribution/wechat/$SLUG"
ZHIHU_DIR="distribution/zhihu/$SLUG"
TOUTIAO_DIR="distribution/toutiao/$SLUG"

mkdir -p "$WECHAT_DIR" "$ZHIHU_DIR" "$TOUTIAO_DIR"

echo "📄 $SLUG"

# ── Step 1: strip front matter ──
BODY_FILE="/tmp/${SLUG}-body.md"
python3 -c "
text = open('$FILE', encoding='utf-8').read()
# 支持 --- 和 +++ 两种 front matter 分隔符
delim = '+++' if '+++' in text else '---'
parts = text.split(delim)
open('$BODY_FILE', 'w', encoding='utf-8').write(parts[2].strip())
print('  body: ' + str(len(parts[2].strip())) + ' chars')
"

# ── Step 2: WeChat — format.py --format wechat ──
echo "  wechat: generating..."
python3 "$FORMATTER" \
  -i "$BODY_FILE" \
  --theme newspaper --format wechat --no-h1 --font-size 16 --no-open \
  --output /tmp/wechat-format 2>&1 | grep -v '^\s*$' || true

# Copy outputs
if [ -d "/tmp/wechat-format/${SLUG}-body" ]; then
  cp "/tmp/wechat-format/${SLUG}-body/article.html" "$WECHAT_DIR/article.html" 2>/dev/null || true
  cp "/tmp/wechat-format/${SLUG}-body/preview.html" "$WECHAT_DIR/preview.html" 2>/dev/null || true
fi

# Strip <a> tags from WeChat output
python3 -c "
import re
for f in ['article.html', 'preview.html']:
    path = '$WECHAT_DIR/' + f
    try:
        html = open(path, encoding='utf-8').read()
        html = re.sub(r'<a\s[^>]*href=\"[^\"]*\"[^>]*>(.*?)</a>', r'\1', html)
        open(path, 'w', encoding='utf-8').write(html)
    except: pass
"
wc -c "$WECHAT_DIR/article.html" 2>/dev/null | awk '{print "  wechat: "$1" bytes"}'

# ── Step 3: Zhihu — format.py --format html ──
echo "  zhihu: generating..."
python3 "$FORMATTER" \
  -i "$BODY_FILE" \
  --theme newspaper --format html --no-h1 --no-open \
  --output /tmp/wechat-format 2>&1 | grep -v '^\s*$' || true

ZHIHU_SRC="/tmp/wechat-format/${SLUG}-body/article.html.html"
if [ -f "$ZHIHU_SRC" ]; then
  cp "$ZHIHU_SRC" "$ZHIHU_DIR/article.html"
  # Compress spacing for zhihu
  python3 -c "
import re
path = '$ZHIHU_DIR/article.html'
html = open(path, encoding='utf-8').read()
html = re.sub(r'margin[^:]*:[^;]+;', '', html)
html = re.sub(r'padding[^:]*:[^;]+;', '', html)
html = re.sub(r'line-height[^:]*:[^;]+;', '', html)
html = re.sub(r'\sdata-darkmode-[^=]+=\"[^\"]*\"', '', html)
html = html.replace('<p ', '<p style=\"margin:0 0 6px\" ')
html = html.replace('<h2 ', '<h2 style=\"margin:10px 0 4px\" ')
html = html.replace('<h3 ', '<h3 style=\"margin:8px 0 3px\" ')
html = html.replace('<pre', '<pre style=\"margin:2px 0\" ')
open(path, 'w', encoding='utf-8').write(html)
"
  wc -c "$ZHIHU_DIR/article.html" | awk '{print "  zhihu: "$1" bytes"}'
else
  echo "  ⚠️ zhihu article.html 未生成"
fi

# ── Step 4: Toutiao — 复用知乎 ──
if [ -f "$ZHIHU_DIR/article.html" ]; then
  cp "$ZHIHU_DIR/article.html" "$TOUTIAO_DIR/article.html"
  wc -c "$TOUTIAO_DIR/article.html" | awk '{print "  toutiao: "$1" bytes"}'
fi

# ── Step 4.5: Zhihu/Toutiao .md (手动粘贴用) ──
python3 -c "
import re, os
text = open('$FILE', encoding='utf-8').read()
body = open('$BODY_FILE', encoding='utf-8').read()

# 取 title + source_url
title_m = re.search(r\"title\s*=\s*'([^']+)'\", text)
title = title_m.group(1) if title_m else '$SLUG'
url_m = re.search(r\"source_url\s*=\s*'([^']+)'\", text)
source_url = url_m.group(1) if url_m else ''

# Zhihu .md — 带 footer
zhihu_md = f'''# {title}

{body}

---

发布于：{os.popen('TZ=Asia/Shanghai date \"+%Y-%m-%d\"').read().strip()}

原文链接：{source_url}

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
'''
with open('$ZHIHU_DIR/${SLUG}.md', 'w', encoding='utf-8') as f:
    f.write(zhihu_md.strip() + chr(10))

# Toutiao .md — 去掉表格 + footer
body_no_table = re.sub(r'^\|.*\|$\n?', '', body, flags=re.MULTILINE)
body_no_table = re.sub(r'^\|[\s\-:]+\|.*\|$\n?', '', body_no_table, flags=re.MULTILINE)
toutiao_md = f'''# {title}

{body_no_table}

---

发布于：{os.popen('TZ=Asia/Shanghai date \"+%Y-%m-%d\"').read().strip()}

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
'''
with open('$TOUTIAO_DIR/${SLUG}.md', 'w', encoding='utf-8') as f:
    f.write(toutiao_md.strip() + chr(10))

print('  zhihu.md: ' + str(os.path.getsize('$ZHIHU_DIR/${SLUG}.md')) + ' bytes')
print('  toutiao.md: ' + str(os.path.getsize('$TOUTIAO_DIR/${SLUG}.md')) + ' bytes')
"

# ── Step 5: Cover — 用 skill 里的 cover-generate.py ──
# 从 front matter 提取标题，拆分三行传给 cover-generate
python3 << PYEOF > /tmp/${SLUG}-cover-args.txt
import re, sys
with open('$FILE', encoding='utf-8') as f:
    text = f.read()
title_m = re.search(r'title\s*=\s*[\'"]([^\'"]+)[\'"]', text)
title = title_m.group(1) if title_m else '$SLUG'
parts = re.split(r'[———]', title, maxsplit=2)
line1 = parts[0].strip() if len(parts) > 0 else title
line2 = parts[1].strip() if len(parts) > 1 else ''
line3 = parts[2].strip() if len(parts) > 2 else ''
print(f"line1='{line1}'")
print(f"line2='{line2}'")
print(f"line3='{line3}'")
PYEOF
source /tmp/${SLUG}-cover-args.txt
python3 /Users/dudu/.hermes/skills/productivity/tech-blog-auto/scripts/cover-generate.py \
  "$SLUG" "$line1" "$line2" "$line3" 2>&1 || echo "  ⚠️ cover 生成失败（不影响 HTML）"

# ── Step 6: Tech diagram — 根据文章系列自动生成架构图 ──
python3 "$ROOT/scripts/generate-diagram.py" "$FILE" "$SLUG" 2>&1 || \
  echo "  ⚠️ diagram 生成跳过（不影响其他输出）"

# ── Step 7: 嵌入配图到文章 ──
DIAGRAM_SVG="$ROOT/distribution/wechat/$SLUG/${SLUG}-diagram.svg"
if [ -f "$DIAGRAM_SVG" ]; then
  # 转 PNG 用于 WeChat（需要系统 cairo 库，没有就跳过）
  DIAGRAM_PNG="$ROOT/distribution/wechat/$SLUG/${SLUG}-diagram.png"
  python3 -c "
import cairosvg
import os
cairosvg.svg2png(url='$DIAGRAM_SVG', write_to='$DIAGRAM_PNG', scale=1.5)
print('  diagram-png: ' + str(os.path.getsize('\"$DIAGRAM_PNG\"') // 1024) + 'KB')
" 2>/dev/null || echo "  ⚠️ diagram-png 跳过（安装 brew install cairo 后可启用）"

  # 嵌入配图引用到 .md 末尾（博客站自动显示）
  python3 << PYEOF
import os
body_file = '$BODY_FILE'
slug = '$SLUG'
with open(body_file, encoding='utf-8') as f:
    body = f.read()
if 'diagram.svg' not in body:
    ref = f'''

> 💡 **排查流程图**

![排查流程图](/{slug}-diagram.svg)
'''
    with open(body_file, 'a', encoding='utf-8') as f:
        f.write(ref)
    print('  diagram: 已嵌入到文章末尾')
PYEOF
fi

# Cleanup
rm -f "$BODY_FILE"

echo ""
echo "✅ format-all.sh done: $SLUG"
