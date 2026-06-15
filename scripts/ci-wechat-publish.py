#!/usr/bin/env python3
"""GitHub Actions 专用：排版文章并发布到公众号草稿箱"""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

def main():
    changed_file = sys.argv[1]  # content/posts/xxx.md
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

    # 去掉 front matter
    text = re.sub(r'^\+{3}\n.*?\+{3}\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^---\n.*?---\n', '', text, flags=re.DOTALL)

    # 插入 H1 标题
    if title:
        text = f'# {title}\n\n' + text.strip()

    # 写临时文件
    tmp = "/tmp/article.md"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text.strip())
    print(f"📝 排版: {title}")

    # 配置 wechat 凭证（从环境变量读取）
    config_path = f"{tool_dir}/config.json"
    with open(config_path, encoding='utf-8') as f:
        config = json.load(f)
    config["wechat"]["app_id"] = os.environ.get("WECHAT_APP_ID", "")
    config["wechat"]["app_secret"] = os.environ.get("WECHAT_APP_SECRET", "")
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 排版
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

    # 找到输出目录
    out_dir = list(Path("/tmp/wechat-out/").glob("*/preview.html"))
    if not out_dir:
        print("❌ 未找到排版输出")
        sys.exit(1)
    article_dir = str(out_dir[0].parent)

    # 发布到公众号草稿箱
    print("🚀 发布到公众号草稿箱...")
    result = subprocess.run(
        ["python3", f"{tool_dir}/scripts/publish.py",
         "--dir", article_dir],
        capture_output=True, text=True, cwd=tool_dir
    )
    print(result.stdout)
    if result.returncode != 0:
        print("❌ 发布失败:", result.stderr)
        sys.exit(1)
    print("✅ 草稿创建成功！登录公众号后台可查看和发布。")

if __name__ == "__main__":
    main()
