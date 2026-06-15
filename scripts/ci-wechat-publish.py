#!/usr/bin/env python3
"""GitHub Actions 专用：排版文章并直接发布到公众号草稿箱"""
import json, os, re, subprocess, sys
from pathlib import Path
import requests

def get_access_token(app_id, app_secret):
    url = "https://api.weixin.qq.com/cgi-bin/token"
    resp = requests.get(url, params={
        "grant_type": "client_credential",
        "appid": app_id,
        "secret": app_secret
    }, timeout=10)
    data = resp.json()
    if "access_token" not in data:
        raise Exception(f"获取 access_token 失败: {data}")
    return data["access_token"]

def create_draft(access_token, title, html_content):
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"
    body = {
        "articles": [{
            "title": title,
            "content": html_content,
            "need_open_comment": 0,
            "only_fans_can_comment": 0
        }]
    }
    resp = requests.post(url, json=body, timeout=15)
    data = resp.json()
    if "media_id" not in data:
        raise Exception(f"创建草稿失败: {data}")
    return data["media_id"]

def main():
    changed_file = sys.argv[1]
    tool_dir = "/tmp/wechat-format-tool"
    os.makedirs("/tmp/wechat-out", exist_ok=True)

    # 读取文章
    with open(changed_file, encoding='utf-8') as f:
        text = f.read()

    # 提取标题
    title = ""
    fm = re.search(r'^\+{3}\n(.*?)\+{3}\n', text, re.DOTALL)
    if fm:
        m = re.search(r'''title\s*=\s*['"](.*?)['"]''', fm.group(1))
        if m:
            title = m.group(1)
    if not title:
        title = Path(changed_file).stem

    print(f"📝 标题: {title}")

    # 去掉 front matter
    text = re.sub(r'^\+{3}\n.*?\+{3}\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^---\n.*?---\n', '', text, flags=re.DOTALL)
    text = f'# {title}\n\n' + text.strip()

    # 写临时文件
    tmp = "/tmp/article.md"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)

    # 配置 wechat 凭证（从环境变量读取）
    config_path = f"{tool_dir}/config.json"
    if not os.path.exists(config_path):
        config = {
            "output_dir": "/tmp/wechat-out",
            "vault_root": "/tmp",
            "settings": {
                "default_theme": "newspaper",
                "auto_open_browser": False,
                "header_author_label": ""
            },
            "wechat": {"app_id": "", "app_secret": "", "author": "后端实战笔记"}
        }
    else:
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
    config["wechat"]["app_id"] = os.environ.get("WECHAT_APP_ID", "")
    config["wechat"]["app_secret"] = os.environ.get("WECHAT_APP_SECRET", "")
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 排版
    print("📐 排版中...")
    result = subprocess.run(
        ["python3", f"{tool_dir}/scripts/format.py",
         "--input", tmp, "--theme", "newspaper",
         "--output", "/tmp/wechat-out/"],
        capture_output=True, text=True, cwd=tool_dir
    )
    print(result.stdout)
    if result.returncode != 0:
        print("❌ 排版失败:", result.stderr)
        sys.exit(1)

    # 找到排版输出的 HTML
    out_dirs = list(Path("/tmp/wechat-out/").glob("*/preview.html"))
    if not out_dirs:
        # 试试 glob 其他路径
        out_dirs = list(Path("/tmp/wechat-out/").rglob("preview.html"))
    if not out_dirs:
        print("❌ 未找到排版输出，目录内容:", list(Path("/tmp/wechat-out/").iterdir()))
        sys.exit(1)

    preview_path = str(out_dirs[0])
    print(f"📄 排版输出: {preview_path}")

    # 读取排版后的 HTML
    with open(preview_path, encoding='utf-8') as f:
        full_html = f.read()

    # 提取正文 HTML（去掉预览页的按钮/工具栏部分）
    # 找到正文区域：在 <h1> 附近
    body_start = full_html.find("<h1")
    body_end = full_html.rfind("</section>")
    if body_start > 0 and body_end > body_start:
        body_html = full_html[body_start:body_end + 10]
    else:
        body_html = full_html

    # 发布到公众号草稿箱
    print("🚀 发布到公众号草稿箱...")
    try:
        token = get_access_token(config["wechat"]["app_id"], config["wechat"]["app_secret"])
        print(f"✅ access_token 获取成功")

        # 上传封面图
        print("📸 上传封面图...")
        cover_path = os.path.join(os.path.dirname(__file__), "..", "static", "cover-default.png")
        # 如果没有默认封面，用第一篇文章的封面图
        if not os.path.exists(cover_path):
            # 用之前生成的 SVG cover
            cover_candidates = [
                "/Users/dudu/workspace-daliy/副业探索/自动化推文流水线/cover-graalvm-comparison.png",
                "/Users/dudu/workspace-daliy/副业探索/自动化推文流水线/cover-graalvm-comparison.svg",
            ]
            for p in cover_candidates:
                if os.path.exists(p):
                    cover_path = p
                    break

        with open(cover_path, "rb") as f:
            r_img = requests.post(
                f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image",
                files={"media": ("cover.png", f, "image/png" if cover_path.endswith('.png') else "image/svg+xml")},
                timeout=15
            )
        img_data = r_img.json()
        if "media_id" not in img_data:
            raise Exception(f"上传封面失败: {img_data}")
        thumb_id = img_data["media_id"]
        print(f"✅ 封面上传成功: {thumb_id}")

        # 读取排版后的 HTML
        with open(preview_path, encoding='utf-8') as f:
            full_html = f.read()

        # 提取正文（从 articleContent div 中提取）
        import html.parser
        class ContentExtractor(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_content = False
                self.depth = 0
                self.parts = []
            def handle_starttag(self, tag, attrs):
                if self.in_content:
                    self.depth += 1
                    attrs_str = ' '.join(f'{k}="{v}"' for k, v in attrs if k)
                    self.parts.append(f'<{tag} {attrs_str}>' if attrs_str else f'<{tag}>')
                elif tag == 'div':
                    for k, v in attrs:
                        if k == 'id' and v == 'articleContent':
                            self.in_content = True
                            self.depth = 0
            def handle_endtag(self, tag):
                if self.in_content:
                    if self.depth > 0:
                        self.parts.append(f'</{tag}>')
                        self.depth -= 1
                    else:
                        self.in_content = False
            def handle_data(self, data):
                if self.in_content:
                    self.parts.append(data)

        extractor = ContentExtractor()
        extractor.feed(full_html)
        body = ''.join(extractor.parts) if extractor.parts else full_html
        print(f"📄 正文长度: {len(body)} 字符")

        # 调微信 API 创建草稿
        draft_body = json.dumps({
            "articles": [{
                "title": title,
                "content": body,
                "thumb_media_id": thumb_id,
                "need_open_comment": 0,
                "only_fans_can_comment": 0
            }]
        }, ensure_ascii=False).encode("utf-8")

        r_draft = requests.post(
            f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}",
            data=draft_body,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        draft_result = r_draft.json()
        if "media_id" in draft_result:
            print(f"✅ 草稿创建成功！media_id: {draft_result['media_id']}")
            print(f"   登录 https://mp.weixin.qq.com/ → 草稿箱 可查看和发布。")
        else:
            raise Exception(f"创建草稿失败: {draft_result}")

    except Exception as e:
        print(f"❌ 发布失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
