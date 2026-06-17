#!/usr/bin/env bash
# 微信公众号混合发布：先试 API 自动发草稿，截断则回退手动粘贴
# 用法: ./scripts/hybrid-wechat-publish.sh content/posts/{slug}.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILE="${1:?用法: hybrid-wechat-publish.sh content/posts/xxx.md}"
if [ ! -f "$FILE" ]; then
  echo "❌ 文件不存在: $FILE" >&2
  exit 1
fi

SLUG="$(basename "$FILE" .md)"
TITLE=$(python3 -c "
import re
text = open('$FILE', encoding='utf-8').read()
m = re.search(r\"title\\s*=\\s*'(.*?)'\", text)
print(m.group(1) if m else '无标题')
")

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  📱 微信公众号混合发布 — 先试API，截断回退手动        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo "📄 $TITLE"
echo ""

# ===== 1. 生成排版预览（同手动方案）=====
echo "⏳ 生成排版预览..."
bash "$ROOT/scripts/manual-wechat-publish.sh" "$FILE" 2>&1 | tail -20

# ===== 2. 尝试 API 自动发布 =====
echo ""
echo "⏳ 尝试 API 自动发布草稿箱..."
WECHAT_ENV="$ROOT/.env.wechat.local"
if [ -f "$WECHAT_ENV" ]; then
  # 用 wewrite 的 publisher 模块创建草稿
  python3 -c "
import sys, os, json
sys.path.insert(0, '$ROOT/scripts/wewrite-toolkit')

# 加载微信密钥
env = {}
with open('$WECHAT_ENV') as f:
    for line in f:
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            env[k] = v.strip().strip('\"').strip(\"'\")

app_id = env.get('WECHAT_APP_ID', '')
app_secret = env.get('WECHAT_APP_SECRET', '')

if not app_id or not app_secret:
    print('⚠️ 未配置微信密钥，跳过 API 发布')
    sys.exit(0)

from publisher import WeChatPublisher
from converter import ArticleConverter

# 读取文章内容
with open('$FILE', encoding='utf-8') as f:
    text = f.read()

# 提取正文（去掉 front matter）
parts = text.split('+++')
body = parts[2].strip() if len(parts) >= 3 else text

# 转换格式
converter = ArticleConverter()
formatted = converter.convert(body, template='newspaper')

# 发布
publisher = WeChatPublisher(app_id, app_secret)
result = publisher.publish(
    title='''$TITLE''',
    content=formatted,
    cover_path='static/covers/${SLUG}.png' if os.path.exists('static/covers/${SLUG}.png') else 'static/cover-default.png'
)

if result and result.get('media_id'):
    print(f'✅ API 发布成功！草稿 media_id: {result[\"media_id\"]}')
    print(f'   字数: {len(formatted)} 字符（限制 20000）')
else:
    print(f'⚠️ API 发布失败: {result}')
    print('   回退方案：手动粘贴预览 HTML')
" 2>&1 || echo "⚠️ API 发布异常，请手动粘贴"
else
  echo "⚠️ 未找到 .env.wechat.local，跳过 API 发布"
  echo "   请手动粘贴预览 HTML 到微信编辑器"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  操作指引                                               ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  1. 浏览器预览已打开 → 检查排版效果                     ║"
echo "║  2. 打开微信『草稿箱』→ 查看刚刚创建的草稿              ║"
echo "║  3. 如果内容完整无截断 → 直接发布                       ║"
echo "║  4. 如果被截断/格式异常 → 回到预览窗口                  ║"
echo "║     → Cmd+A → Cmd+C → 微信编辑器粘贴                    ║"
echo "╚══════════════════════════════════════════════════════════╝"