#!/usr/bin/env bash
# 微信公众号手动发布工具 | 主题可选 | 预览 → 复制 → 发布
# 用法: ./scripts/manual-wechat-publish.sh content/posts/{slug}.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILE="${1:?用法: manual-wechat-publish.sh content/posts/xxx.md}"
if [ ! -f "$FILE" ]; then
  echo "❌ 文件不存在: $FILE" >&2
  exit 1
fi

SLUG="$(basename "$FILE" .md)"
TITLE=$(grep '^title\s*=' "$FILE" | head -1 | sed "s/.*= *'//; s/'$//")
FORMATTER="$HOME/.hermes/skills/xiaohu-wechat-format/scripts/format.py"

OUT_DIR="distribution/wechat/$SLUG"
mkdir -p "$OUT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║      📱 微信公众号手动发布 — 后端实战笔记              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "📄 文章: $TITLE"
echo "📁 输出: $OUT_DIR/"
echo ""

# ── 主题选择 ──
THEME_FILE="$ROOT/.wechat-default-theme"
SAVED_THEME="newspaper"
[ -f "$THEME_FILE" ] && SAVED_THEME=$(cat "$THEME_FILE")

echo "┌─ 选择主题 ──────────────────────────────────────────┐"
echo "│  1) 使用已保存主题: $SAVED_THEME              │"
echo "│  2) 打开画廊挑选（推荐，可预览切换 88 款主题）    │"
echo "│  3) 输入主题名                                     │"
echo "└────────────────────────────────────────────────────┘"
read -p "选择 [1-3] (默认 1): " CHOICE
CHOICE=${CHOICE:-1}

THEME=""
if [ "$CHOICE" = "2" ]; then
    echo ""
    echo "⏳ 正在生成画廊..."
    # 提取正文到临时文件
    BODY_FILE="/tmp/wechat-preview/${SLUG}.md"
    mkdir -p /tmp/wechat-preview
    python3 -c "
text = open('$FILE', encoding='utf-8').read()
parts = text.split('+++')
body = parts[2].strip() if len(parts) >= 3 else text
open('$BODY_FILE', 'w', encoding='utf-8').write(body)
"

    python3 "$FORMATTER" \
        -i "$BODY_FILE" \
        --gallery \
        --no-h1 \
        --recommend newspaper magazine github bytedance ink midnight \
        --output /tmp/wechat-format \
        --no-open 2>&1 | grep -v '^\s'

    rm -f "$BODY_FILE"

    # 等待用户选完主题
    echo ""
    echo "┌──────────────────────────────────────────────────┐"
    echo "│  画廊已打开！                                     │"
    echo "│  左侧选主题 → 点「用这个风格排版」→ 回终端按回车 │"
    echo "└──────────────────────────────────────────────────┘"
    read -p "按回车继续..."

    # 读取选中的主题
    if [ -f /tmp/wechat-format/selected-theme.txt ]; then
        THEME=$(cat /tmp/wechat-format/selected-theme.txt)
        echo "✅ 选中主题: $THEME"
        echo "$THEME" > "$THEME_FILE"
    else
        THEME="$SAVED_THEME"
        echo "⚠️ 未检测到主题选择，使用默认: $THEME"
    fi
elif [ "$CHOICE" = "3" ]; then
    read -p "输入主题名 (如 newspaper, github, midnight): " THEME
    THEME=${THEME:-newspaper}
    echo "$THEME" > "$THEME_FILE"
    echo "✅ 已保存默认主题: $THEME"
else
    THEME="$SAVED_THEME"
    echo "✅ 使用已保存主题: $THEME"
fi

# ── 提取正文（如果还没提取）──
BODY_FILE="/tmp/wechat-preview/${SLUG}.md"
mkdir -p /tmp/wechat-preview
python3 -c "
text = open('$FILE', encoding='utf-8').read()
parts = text.split('+++')
body = parts[2].strip() if len(parts) >= 3 else text
open('$BODY_FILE', 'w', encoding='utf-8').write(body)
print(f'✅ 正文提取: {len(body)} 字符')
"

# ── 排版 ──
echo ""
echo "⏳ 排版中（主题: $THEME, --no-h1）..."
TMP_OUT="/tmp/wechat-format/$SLUG"
rm -rf "$TMP_OUT"

python3 "$FORMATTER" \
    -i "$BODY_FILE" \
    --theme "$THEME" \
    --format wechat \
    --no-h1 \
    --output /tmp/wechat-format \
    --font-size 16 \
    --no-open 2>&1 | grep -v '^\s'

# ── 复制结果到输出目录 ──
if [ -f "$TMP_OUT/article.html" ]; then
    cp "$TMP_OUT/article.html" "$OUT_DIR/article.html"
    
    # 微信版本清理外部链接（微信编辑器弹拦截）
    python3 -c "
import re
path = '$OUT_DIR/article.html'
html = open(path, encoding='utf-8').read()
# 去掉脚注 URL
html = re.sub(r':\\s*https?://[^\\s<>\\\"\\'\\]\\)<]+', '', html)
# 去掉 <a> 标签保留文本
html = re.sub(r'<a\\s[^>]*>', '', html)
html = re.sub(r'</a>', '', html)
open(path, 'w', encoding='utf-8').write(html)
print('✅ 微信版已清理外部链接')
" 2>&1 | grep -v '^$'
    
    cat "$OUT_DIR/article.html" | pbcopy
    echo "✅ article.html → $OUT_DIR/（已复制到剪贴板）"
fi

# 兼容旧版：从子目录查找
FOUND_PREVIEW=""
[ -f "$TMP_OUT/preview.html" ] && FOUND_PREVIEW="$TMP_OUT/preview.html"
[ -z "$FOUND_PREVIEW" ] && FOUND_PREVIEW=$(find "$TMP_OUT" -name "preview.html" 2>/dev/null | head -1)

if [ -n "$FOUND_PREVIEW" ]; then
    cp "$FOUND_PREVIEW" "$OUT_DIR/preview.html"
    
    # 清理 preview.html 的外部链接
    python3 -c "
import re
path = '$OUT_DIR/preview.html'
html = open(path, encoding='utf-8').read()
html = re.sub(r':\s*https?://[^\s<>\x22\x27\]\)<]+', '', html)
html = re.sub(r'<a\s[^>]*>', '', html)
html = re.sub(r'</a>', '', html)
open(path, 'w', encoding='utf-8').write(html)
print('✅ preview.html 已清理链接')
" 2>&1 | grep -v '^$'
    
    # 去掉 H1（兼容旧版 format.py）

    python3 -c "
from bs4 import BeautifulSoup
html = open('$OUT_DIR/preview.html', encoding='utf-8').read()
soup = BeautifulSoup(html, 'html.parser')
for h1 in soup.find_all('h1'):
    h1.decompose()
open('$OUT_DIR/preview.html', 'w', encoding='utf-8').write(str(soup))
" 2>/dev/null || true
    echo "✅ preview.html → $OUT_DIR/"
fi

# ── 生成知乎/头条分发文件（--format html 干净输出，保留链接）──
echo "⏳ 生成多平台分发..."
for platform in zhihu toutiao; do
    PDIR="distribution/$platform/$SLUG"
    mkdir -p "$PDIR"
    
    python3 "$FORMATTER" \
        -i "$BODY_FILE" \
        --theme "$THEME" \
        --format html \
        --no-h1 \
        --output /tmp/wechat-format \
        --no-open 2>&1 | grep -v '^\s' || true
    
    # 查找生成的 html（format.py 按输入文件名创建子目录）
    SRC=$(find "/tmp/wechat-format/$SLUG" -name "article.html.html" -o -name "article.html" 2>/dev/null | head -1)
    if [ -n "$SRC" ]; then
        cp "$SRC" "$PDIR/article.html"
        echo "✅ $platform → $PDIR/"
    fi
done
COVER_PATH="$OUT_DIR/${SLUG}-cover.png"
echo "⏳ 封面图..."
python3 -c "
from PIL import Image, ImageDraw, ImageFont
import os, sys
title = sys.argv[1]
W, H = 900, 500
FONT = '/System/Library/Fonts/PingFang.ttc'
if not os.path.exists(FONT):
    FONT = '/System/Library/Fonts/STHeiti Light.ttc' if os.path.exists('/System/Library/Fonts/STHeiti Light.ttc') else None
try:
    img = Image.new('RGB', (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(50 + (20-50) * y / H)
        g = int(104 + (60-104) * y / H)
        b = int(145 + (100-145) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    if FONT:
        fs = 42 if len(title) <= 15 else (36 if len(title) <= 25 else 30)
        font = ImageFont.truetype(FONT, fs)
        tw = draw.textlength(title, font=font)
        if tw > W - 120:
            mid = len(title) // 2
            draw.text(((W-draw.textlength(title[:mid], font=font))/2, H//2-fs-10), title[:mid], fill=(255,255,255), font=font)
            draw.text(((W-draw.textlength(title[mid:], font=font))/2, H//2+5), title[mid:], fill=(255,255,255), font=font)
        else:
            draw.text(((W-tw)/2, H//2-fs//2), title, fill=(255,255,255), font=font)
        bf = ImageFont.truetype(FONT, 18)
        bw = draw.textlength('后端实战笔记', font=bf)
        draw.text(((W-bw)/2, H-50), '后端实战笔记', fill=(200,220,240), font=bf)
    img.save('$COVER_PATH')
    print(f'✅ 封面: {os.path.getsize(\"$COVER_PATH\")//1024}KB')
except Exception as e:
    print(f'⚠️ 封面跳过: {e}')
" "$TITLE"

# ── 技术架构图 (复用 format-all.sh 的 diagram 生成) ──
python3 "$ROOT/scripts/generate-diagram.py" "$FILE" "$SLUG" 2>&1 || true

# ── 打开预览 ──
if [ -f "$OUT_DIR/preview.html" ]; then
    echo "🌐 打开预览..."
    open "$OUT_DIR/preview.html" 2>/dev/null || true
fi

# 清理临时文件
rm -f "$BODY_FILE"
rm -rf "$TMP_OUT"

# ── 完成 ──
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ 准备就绪                                           ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  1. 预览窗口已打开，检查排版                            ║"
echo "║  2. mp.weixin.qq.com → 草稿箱 → 新建图文               ║"
echo "║  3. Cmd+V 粘贴（内容已在剪贴板）                        ║"
echo "║  4. 封面上传: $COVER_PATH               ║"
echo "║  5. 保存 → 群发                                         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "📦 素材: $OUT_DIR/"
echo "🎨 下次默认主题: $THEME (修改: echo '主题名' > .wechat-default-theme)"
echo "✅ $(date '+%H:%M:%S')"
