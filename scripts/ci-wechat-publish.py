#!/usr/bin/env python3
"""GitHub Actions: format article + publish to WeChat draft box."""
import html.parser, json, os, re, subprocess, sys
from pathlib import Path
import requests

TOOL_DIR = "/tmp/wechat-format-tool"

def fix_format_py(slug):
    """修复 format.py 的语法高亮 bug：将注释高亮移到最后"""
    fp = f"{TOOL_DIR}/scripts/format.py"
    if not os.path.exists(fp):
        return False
    with open(fp) as f:
        code = f.read()
    # 检查是否已修复
    if "# 注释放最后" in code:
        return True
    # 找到 "def _basic_syntax_highlight" 函数
    # 策略：在 return code_html 前添加注释高亮，然后原位置删除注释高亮
    # 用简单方法：找到注释块开头和 return，交换位置

    lines = code.split('\n')
    # 找到三行标记位置
    comment_start = None  # '# 单行注释'
    hash_start = None     # "r'#[^{]"
    return_idx = None     # 'return code_html'
    decorator_end = None  # 装饰器后面的空行

    for i, line in enumerate(lines):
        if decorator_end is None and '(#c586c0)' in line and '装饰器' in lines[i-1]:
            decorator_end = i + 2
        if comment_start is None and '单行注释' in line:
            comment_start = i
        if hash_start is None and "r'#[^{]" in line:
            hash_start = i
        if return_idx is None and 'return code_html' in line and i > 50:
            return_idx = i

    if comment_start is None or return_idx is None:
        return False

    # 找到注释块结束（到 return 之前）
    # 提取注释块
    comment_lines = []
    j = comment_start
    while j < return_idx:
        comment_lines.append(lines[j])
        j += 1
    # 注释块 + 后面的空白行
    comment_block = '\n'.join(comment_lines).rstrip()

    # 在 return 前插入 # 注释放最后 + 注释块
    indent = '    '
    insert_block = f"{indent}# 注释放最后——避免数字/关键字高亮污染\n{comment_block}"

    # 从原位置删除注释块
    new_lines = lines[:comment_start]
    # 跳过原有注释行和空白行
    j = comment_start
    while j < return_idx:
        j += 1
    new_lines.extend(lines[j:])

    # 在 return 前插入
    for i in range(len(new_lines) - 1, -1, -1):
        if new_lines[i].strip().startswith('return code_html'):
            new_lines.insert(i, insert_block)
            break

    with open(fp, 'w') as f:
        f.write('\n'.join(new_lines))
    return True


def extract_title(text):
    fm = re.search(r'^\+{3}\n(.*?)\+{3}\n', text, re.DOTALL)
    if fm:
        m = re.search(r'''title\s*=\s*['"](.*?)['"]''', fm.group(1))
        if m:
            return m.group(1)
    return Path(sys.argv[1]).stem


class ContentExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_content = False
        self.depth = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if self.in_content:
            self.depth += 1
            a = ' '.join(f'{k}="{v}"' for k, v in attrs if k)
            self.parts.append(f'<{tag} {a}>' if a else f'<{tag}>')
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


def main():
    # 读取文章
    filepath = sys.argv[1]
    slug = Path(filepath).stem
    with open(filepath, encoding='utf-8') as f:
        text = f.read()

    title = extract_title(text)
    print(f"📝 {title}")

    # 去重检查
    tracking_file = "data/wechat-published.json"
    published = []
    if os.path.exists(tracking_file):
        with open(tracking_file) as f:
            published = json.load(f)
    if slug in published:
        print(f"⏭️ 已发布过，跳过")
        return
    print(f"📋 slug: {slug}")

    # 去掉 front matter
    text = re.sub(r'^\+{3}\n.*?\+{3}\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^---\n.*?---\n', '', text, flags=re.DOTALL)
    tmp = f"/tmp/article-{slug}.md"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text.strip())

    # 配置
    config_path = f"{TOOL_DIR}/config.json"
    if not os.path.exists(config_path):
        config = {
            "output_dir": "/tmp/wechat-out",
            "vault_root": "/tmp",
            "settings": {"default_theme": "newspaper",
                         "auto_open_browser": False,
                         "header_author_label": ""},
            "wechat": {"app_id": "", "app_secret": "",
                       "author": "后端实战笔记"}
        }
    else:
        with open(config_path) as f:
            config = json.load(f)
    config["wechat"]["app_id"] = os.environ.get("WECHAT_APP_ID", "")
    config["wechat"]["app_secret"] = os.environ.get("WECHAT_APP_SECRET", "")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 修复 format.py bug
    if fix_format_py():
        print("✅ 已修复 format.py 语法高亮")

    # 排版
    print("📐 排版中...")
    r = subprocess.run(
        ["python3", f"{TOOL_DIR}/scripts/format.py",
         "--input", tmp, "--theme", "newspaper",
         "--output", "/tmp/wechat-out/", "--no-open"],
        capture_output=True, text=True, cwd=TOOL_DIR
    )
    print(r.stdout)
    if r.returncode != 0:
        print("❌ 排版失败:", r.stderr); sys.exit(1)

    # 找输出
    out = list(Path("/tmp/wechat-out/").rglob("preview.html"))
    if not out:
        print("❌ 未找到排版输出"); sys.exit(1)
    preview_path = str(out[0])
    print(f"📄 {preview_path}")

    # 提取正文
    with open(preview_path) as f:
        html = f.read()
    ex = ContentExtractor()
    ex.feed(html)
    body = ''.join(ex.parts) if ex.parts else html
    print(f"📄 正文: {len(body)} 字符")

    # 发布
    print("🚀 发布到公众号草稿箱...")
    try:
        # 获取 token
        r = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
            "grant_type": "client_credential",
            "appid": config["wechat"]["app_id"],
            "secret": config["wechat"]["app_secret"]
        }, timeout=10)
        td = r.json()
        if "access_token" not in td:
            # 打印完整错误
            print(f"❌ token 失败: {td}")
            # 尝试自己加 IP 到白名单
            import socket
            my_ip = socket.gethostbyname(socket.gethostname())
            # 或者从环境变量获取
            my_ip = os.environ.get("RUNNER_IP", my_ip)
            # 也试试从 ipify 获取
            try:
                my_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
            except:
                pass
            print(f"当前 IP: {my_ip}, 需要加到微信IP白名单: {my_ip}/32")
            sys.exit(1)
        token = td["access_token"]

        # 上传封面
        cover = "static/cover-default.png"
        if os.path.exists(cover):
            with open(cover, "rb") as f:
                r2 = requests.post(
                    "https://api.weixin.qq.com/cgi-bin/material/add_material"
                    f"?access_token={token}&type=image",
                    files={"media": ("cover.png", f, "image/png")},
                    timeout=15)
            thumb_id = r2.json().get("media_id", "")
            if thumb_id:
                print(f"📸 封面上传成功")
            else:
                print(f"⚠️ 封面上传失败: {r2.json()}")
                thumb_id = ""
        else:
            thumb_id = ""

        # 创建草稿
        draft = {
            "articles": [{
                "title": title,
                "content": body,
                "need_open_comment": 0,
                "only_fans_can_comment": 0
            }]
        }
        if thumb_id:
            draft["articles"][0]["thumb_media_id"] = thumb_id

        # 内容大小限制：微信限制 < 2万字符，超长截断
        content_body = draft["articles"][0]["content"]
        if len(content_body) > 18000:
            print(f"⚠️ 内容过长 ({len(content_body)} 字符)，截断至 18000")
            draft["articles"][0]["content"] = content_body[:18000]

        r3 = requests.post(
            f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}",
            data=json.dumps(draft, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        dr = r3.json()
        if "media_id" in dr:
            print(f"✅ 草稿创建成功！media_id: {dr['media_id']}")
            # 记录已发布
            published.append(slug)
            with open(tracking_file, 'w') as f:
                json.dump(published, f, indent=2, ensure_ascii=False)
            print(f"📋 已记录 {slug}，下次跳过")
        else:
            print(f"❌ 创建草稿失败: {dr}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ 发布失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
