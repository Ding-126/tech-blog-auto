#!/usr/bin/env bash
# ============================================================
# 分发文件质量门禁 — 发布前的 4 项强制检查
# 用法: ./scripts/validate-distribution.sh content/posts/xxx.md
# 所有检查通过返回 0，任一失败返回非 0
# ============================================================
set -euo pipefail

FILE="${1:?用法: validate-distribution.sh content/posts/xxx.md}"
SLUG="$(basename "$FILE" .md)"

# 从 front matter 提取系列序号（如果有）
SERIES_NUM=$(python3 -c "
import re
text = open('$FILE', encoding='utf-8').read()
m = re.search(r'series_number\s*=\s*(\d+)', text)
print(m.group(1) if m else '0')
")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'
FAIL=0

echo "🔍 验证: $SLUG"
echo ""

# ── 检查1: distribution 目录存在 ──
for platform in wechat zhihu toutiao; do
    DIR="distribution/$platform/$SLUG"
    if [ -d "$DIR" ]; then
        echo -e "  ${GREEN}✅${NC} $DIR 存在"
    else
        echo -e "  ${RED}❌${NC} $DIR 不存在"
        FAIL=1
    fi
done

# ── 检查2: 三平台 article.html 都存在 ──
ALL_HTML_EXIST=0
for platform in wechat zhihu toutiao; do
    HTML="distribution/$platform/$SLUG/article.html"
    if [ -f "$HTML" ]; then
        echo -e "  ${GREEN}✅${NC} $HTML 存在 ($(stat -f%z "$HTML" 2>/dev/null) bytes)"
    else
        echo -e "  ${RED}❌${NC} $HTML 不存在！"
        ALL_HTML_EXIST=1
    fi
done
if [ "$ALL_HTML_EXIST" -ne 0 ]; then
    FAIL=1
fi

# ── 检查3: WeChat article.html 无 front matter ──
WECHAT_HTML="distribution/wechat/$SLUG/article.html"
if [ -f "$WECHAT_HTML" ]; then
    FIRST_LINE=$(head -c 30 "$WECHAT_HTML")
    if echo "$FIRST_LINE" | grep -q '^<section'; then
        echo -e "  ${GREEN}✅${NC} article.html 无 front matter（以 <section 开头）"
    elif echo "$FIRST_LINE" | grep -q '^\+\+\+'; then
        echo -e "  ${RED}❌${NC} article.html 有 front matter 泄露！需用 strip 后的 body 重新生成"
        FAIL=1
    else
        echo -e "  ${YELLOW}⚠${NC} article.html 首行未知: $FIRST_LINE"
    fi
else
    echo -e "  ${RED}❌${NC} $WECHAT_HTML 不存在"
    FAIL=1
fi

# ── 检查4: WeChat 文章无 <a> 标签（非 qq 超链接） ──
if [ -f "$WECHAT_HTML" ]; then
    LINK_COUNT=$(grep -c 'href=' "$WECHAT_HTML" 2>/dev/null || echo "0")
    LINK_COUNT=$(echo "$LINK_COUNT" | tr -d '[:space:]' | tail -1)
    if [ "${LINK_COUNT:-0}" -eq 0 ] 2>/dev/null; then
        echo -e "  ${GREEN}✅${NC} WeChat 文章无 <a> 标签（$LINK_COUNT 个链接）"
    else
        echo -e "  ${RED}❌${NC} WeChat 文章有 $LINK_COUNT 个 <a> 标签残留！需剥离"
        FAIL=1
    fi
fi

# ── 检查5: zhihu/toutiao .md 文件在子目录内（wechat 发布用 article.html 不需要 .md）──
for platform in zhihu toutiao; do
    SUB_MD="distribution/$platform/$SLUG/$SLUG.md"
    ROOT_MD="distribution/$platform/$SLUG.md"
    if [ -f "$ROOT_MD" ] && [ ! -f "$SUB_MD" ]; then
        mv "$ROOT_MD" "$SUB_MD"
        echo -e "  ${YELLOW}⚠${NC} 修复: $ROOT_MD → $SUB_MD"
    fi
    if [ -f "$SUB_MD" ]; then
        echo -e "  ${GREEN}✅${NC} $SUB_MD 存在"
    elif [ ! -f "$SUB_MD" ] && [ ! -f "$ROOT_MD" ]; then
        echo -e "  ${YELLOW}⚠${NC} $SUB_MD 不存在（不影响发布，但数据不完整）"
    fi
done

# ── 检查6: 封面图存在且文字已渲染 ──
COVER=$(find "distribution/wechat/$SLUG" -name '*-cover.png' 2>/dev/null | head -1)
if [ -n "$COVER" ] && [ -s "$COVER" ]; then
    COVER_SIZE=$(stat -f%z "$COVER" 2>/dev/null || echo 0)
    if [ "$COVER_SIZE" -gt 8000 ]; then
        echo -e "  ${GREEN}✅${NC} 封面图存在 ($(basename "$COVER"), ${COVER_SIZE} bytes)"
    else
        echo -e "  ${RED}❌${NC} 封面图仅 ${COVER_SIZE} bytes，中文可能未渲染！需用 PingFang.ttc 重新生成"
        FAIL=1
    fi
else
    echo -e "  ${RED}❌${NC} 封面图不存在"
    FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}✅ 全部检查通过${NC}"
else
    echo -e "${RED}❌ 有 $FAIL 项检查失败，修好后再发布${NC}"
fi
exit "$FAIL"
