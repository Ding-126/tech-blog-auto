#!/usr/bin/env bash
# 自动发布文章到微信公众号草稿箱
# 需要先开通微信公众平台开发者模式
# 使用: ./scripts/publish-wechat-draft.sh content/posts/xxx.md
#
# 前置准备（只需做一次）：
# 1. 登录 https://mp.weixin.qq.com/ → 设置与开发 → 基本配置
# 2. 点击「成为开发者」→ 获取 AppID 和 AppSecret
# 3. 在「IP白名单」中添加本机外网 IP
# 4. 将 AppID 和 AppSecret 写入 blog-repo/.env 文件：
#    WECHAT_APP_ID=your_app_id
#    WECHAT_APP_SECRET=your_app_secret

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "❌ 未找到 .env 文件。请先配置微信开发者凭证。"
  echo "   参考脚本顶部注释操作。"
  exit 0
fi

set -a; source .env; set +a

if [ -z "${WECHAT_APP_ID:-}" ] || [ -z "${WECHAT_APP_SECRET:-}" ]; then
  echo "❌ .env 中缺少 WECHAT_APP_ID 或 WECHAT_APP_SECRET"
  echo "   格式："
  echo "   WECHAT_APP_ID=your_app_id"
  echo "   WECHAT_APP_SECRET=your_app_secret"
  exit 0
fi

FILE="${1:?用法: publish-wechat-draft.sh content/posts/xxx.md}"
if [ ! -f "$FILE" ]; then
  echo "❌ 文件不存在: $FILE"; exit 1
fi

SLUG="$(basename "$FILE" .md)"
HTML_FILE="$ROOT/distribution/wechat/${SLUG}.html"

TITLE=$(python3 -c "
import re
text = open('$FILE', encoding='utf-8').read()
m = re.search(r\"title\\s*=\\s*'(.*?)'\", text)
print(m.group(1) if m else '文章')
")

echo "=== 发布到公众号草稿箱 ==="
echo "标题: $TITLE"

# 1. 获取 access_token
echo "① 获取 API 凭证..."
TOKEN_RESP=$(curl -s "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${WECHAT_APP_ID}&secret=${WECHAT_APP_SECRET}")
ACCESS_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$ACCESS_TOKEN" ]; then
  echo "❌ 获取 access_token 失败:"
  echo "$TOKEN_RESP"
  exit 1
fi
echo "✅ 凭证获取成功"

# 2. 确保 HTML 存在
if [ ! -f "$HTML_FILE" ]; then
  echo "⚠️ 生成分发文件..."
  ./scripts/distribute-post.sh "$FILE"
fi

# 3. 读取 HTML 并序列化为 JSON 字符串
echo "② 准备文章内容..."
HTML_CONTENT=$(python3 -c "
import json
with open('$HTML_FILE', encoding='utf-8') as f:
    text = f.read()
print(json.dumps(text))
")

# 4. 创建草稿
echo "③ 创建公众号草稿..."
DRAFT_BODY=$(python3 -c "
import json
body = {
    'articles': [{
        'title': '$TITLE',
        'content': $HTML_CONTENT,
        'need_open_comment': 0,
        'only_fans_can_comment': 0
    }]
}
print(json.dumps(body, ensure_ascii=False))
")

RESP=$(curl -s -X POST \
  "https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$DRAFT_BODY")

MEDIA_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('media_id','ERROR: '+json.dumps(d)))")
echo "=== 结果 ==="
if echo "$MEDIA_ID" | grep -q "^ERROR"; then
  echo "❌ $MEDIA_ID"
  echo "排查："
  echo "  - AppID/AppSecret 是否正确？"
  echo "  - IP 白名单是否加了本机外网 IP？"
  echo "  - 公众号是否已开通开发模式？"
else
  echo "✅ 草稿创建成功！media_id: $MEDIA_ID"
  echo "   登录 https://mp.weixin.qq.com/ → 草稿箱 可查看和发布。"
fi
