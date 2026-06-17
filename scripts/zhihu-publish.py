#!/usr/bin/env python3
"""知乎专栏自动发布：复用排版管线，通过 Cookie 调内部 API

用法:
  # 需要先设置环境变量或 .env.zhihu 文件:
  #   ZHIHU_COOKIE="2|1:0|10:..."     (从浏览器导出的 z_c0 cookie)
  #   ZHIHU_XSRF="..."                 (_xsrf token)
  #   ZHIHU_COLUMN_ID="专栏ID"         (可选，专栏 slug/ID)
  
  python3 zhihu-publish.py content/posts/article.md

工作原理:
  1. 复用 ci-wechat-publish.py 的 format_article() + style_inject()
  2. 用 Cookie 调知乎内部 API 创建专栏文章
  3. 同一套 styled HTML，样式保持一致
"""
import json, os, re, sys
from pathlib import Path
import requests

# 复用管线
sys.path.insert(0, os.path.dirname(__file__) or ".")

# ── 配置 ──────────────────────────────────────────

def load_env(path: str = ".env.zhihu"):
    """载入 .env.zhihu 环境变量"""
    env_file = Path(path)
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_config() -> tuple:
    """获取知乎配置，优先环境变量"""
    cookie = os.environ.get("ZHIHU_COOKIE", "")
    xsrf = os.environ.get("ZHIHU_XSRF", "")
    column = os.environ.get("ZHIHU_COLUMN_ID", "")
    return cookie, xsrf, column


# ══════════════════════════════════════════════════
#  知乎 API
# ══════════════════════════════════════════════════

def publish_article(title: str, content_html: str, cookie: str, xsrf: str, column_id: str = "") -> dict:
    """发布文章到知乎专栏，返回 API 响应"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Cookie": f'z_c0="{cookie}"; _xsrf={xsrf}',
        "x-xsrf-token": xsrf,
        "x-requested-with": "fetch",
        "Content-Type": "application/json;charset=utf-8",
        "Referer": "https://zhuanlan.zhihu.com/",
        "Origin": "https://zhuanlan.zhihu.com",
    }

    # 构建发布数据
    data = {
        "title": title,
        "content": content_html,
        "can_reply": True,
        "comment_permission": "all",
        "article_type": "article",
    }
    if column_id:
        data["column_id"] = column_id

    # 尝试多个知乎 API 端点
    endpoints = [
        "https://zhuanlan.zhihu.com/api/articles",
        "https://www.zhihu.com/api/v5/articles",
    ]
    
    for ep in endpoints:
        r = requests.post(ep, headers=headers, json=data, timeout=30)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 403:
            # 403 = 需要认证但被拒绝，继续试下一个端点
            continue
        else:
            # 404/其他错误
            continue
    
    # 全部失败
    print("❌ 所有 API 端点都失败:")
    for ep in endpoints:
        r = requests.post(ep, headers=headers, json=data, timeout=15)
        print(f"  {ep}: HTTP {r.status_code}")
        if r.text:
            print(f"     {r.text[:200]}")
    print()
    print("💡 可能原因:")
    print("  1. Cookie 过期（重新从浏览器导出 z_c0）")
    print("  2. 需要在浏览器中先手动发布一篇文章激活专栏")
    print("  3. 从你自己的 Mac 运行（当前环境 IP 可能被限制）")
    sys.exit(1)


# ══════════════════════════════════════════════════
#  整合管线
# ══════════════════════════════════════════════════

def main():
    load_env()  # 载入 .env.zhihu
    cookie, xsrf, column_id = get_config()

    if not cookie or not xsrf:
        print("❌ 请先设置 ZHIHU_COOKIE 和 ZHIHU_XSRF")
        print("   浏览器登录 zhihu.com → F12 → Application → Cookies")
        print("   复制 z_c0 和 _xsrf 的值")
        print("   存到 .env.zhihu 文件:")
        print('     ZHIHU_COOKIE="2|1:0|10:..."')
        print('     ZHIHU_XSRF="..."')
        print('     ZHIHU_COLUMN_ID="专栏ID(可选)"')
        sys.exit(1)

    filepath = sys.argv[1]

    # 复用 ci-wechat-publish 的排版和样式
    # 动态导入来避免硬依赖
    sys.path.insert(0, os.path.dirname(filepath) or ".")

    md_text = Path(filepath).read_text(encoding="utf-8")

    # 提取标题
    title = ""
    fm = re.search(r'^\+\+\+\n(.*?)\+\+\+\n', md_text, re.DOTALL)
    if fm:
        m = re.search(r"title\s*=\s*['\"](.*?)['\"]", fm.group(1))
        if m:
            title = m.group(1)
    if not title:
        title = Path(filepath).stem

    # 复用 format_article 和 style_inject
    import importlib.util
    cwp_path = os.path.join(os.path.dirname(__file__) or ".", "ci-wechat-publish.py")
    spec = importlib.util.spec_from_file_location("cwp", cwp_path)
    cwp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cwp)
    format_article = cwp.format_article
    style_inject = cwp.style_inject

    html, title, slug = format_article(filepath)
    styled_html = style_inject(html)
    # 知乎无字数限制，不过载
    print(f"📝 {title}")
    print(f"📏 内容: {len(styled_html)} 字符")

    print("🚀 发布到知乎专栏...")
    result = publish_article(title, styled_html, cookie, xsrf, column_id)

    url = result.get("url", "")
    if url:
        print(f"✅ 发布成功: {url}")
    else:
        print(f"✅ 发布成功: {json.dumps(result, ensure_ascii=False)[:200]}")

    # 记录发布（可选）
    tracking = "data/zhihu-published.json"
    published = []
    if os.path.exists(tracking):
        with open(tracking) as f:
            published = json.load(f)
    if slug not in published:
        published.append(slug)
        Path(tracking).parent.mkdir(parents=True, exist_ok=True)
        with open(tracking, "w") as f:
            json.dump(published, f, indent=2, ensure_ascii=False)
        print(f"📋 已记录 {slug}，下次跳过")


if __name__ == "__main__":
    main()
