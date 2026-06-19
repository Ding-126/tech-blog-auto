#!/usr/bin/env python3
"""
Auto-generate technical diagrams for article series.
Usage: python3 scripts/generate-diagram.py content/posts/{slug}.md {slug}

Looks up series_name from front matter → diagram-configs.json → generates SVG
Saves to distribution/wechat/{slug}/{slug}-diagram.svg
"""
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))

# --- paths ---
GENERATOR = os.path.join(
    os.path.expanduser("~/.hermes/skills/fireworks-tech-graph/scripts"),
    "generate-from-template.py",
)
CONFIG_FILE = os.path.join(SCRIPT_DIR, "diagram-configs.json")


def read_front_matter(filepath: str) -> dict:
    """Extract key fields from Hugo/Toml front matter."""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    fm = {}

    # series_name — match both '单引号' and "双引号"
    m = re.search(r'series_name\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    if m:
        fm["series_name"] = m.group(1).strip()

    # title
    m = re.search(r'title\s*=\s*[\'"]([^\'"]+)[\'"]', text)
    if m:
        fm["title"] = m.group(1).strip()

    # series_number (optional)
    m = re.search(r"series_number\s*=\s*(\d+)", text)
    if m:
        fm["series_number"] = int(m.group(1))

    return fm


def find_series_config(series_name: str, configs: dict):
    """Match series_name (possibly partial) against config keys."""
    if not series_name:
        return None

    # exact match
    if series_name in configs.get("series", {}):
        return configs["series"][series_name]

    # prefix match: "Redis 系列一二三" → matches "Redis"
    for key in configs.get("series", {}):
        if series_name.startswith(key) or key.startswith(series_name):
            return configs["series"][key]

    return None


def generate_diagram(post_file: str, slug: str):
    """Generate a diagram SVG and return its path, or None."""
    if not os.path.exists(GENERATOR):
        print(f"  ⚠️  diagram generator not found: {GENERATOR}")
        return None

    fm = read_front_matter(post_file)
    series_name = fm.get("series_name", "")

    # load configs
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            configs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  ⚠️  diagram configs error: {e}")
        return None

    # find matching config
    cfg = find_series_config(series_name, configs)
    if cfg is None:
        cfg = configs.get("fallback")
        if not cfg:
            return None

    # output dir
    out_dir = os.path.join(ROOT, "distribution", "wechat", slug)
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, f"{slug}-diagram.svg")

    # build title from article title
    article_title = fm.get("title", slug)
    diagram_title = article_title  # article title already contains series context

    # build data JSON
    data = {
        "title": diagram_title,
        "style": cfg.get("style", 1),
        "width": cfg.get("width", 960),
        "height": cfg.get("height", 600),
        "nodes": cfg.get("nodes", []),
        "arrows": cfg.get("arrows", []),
    }

    # add legend if specified
    if cfg.get("legend"):
        data["legend"] = cfg["legend"]

    data_json = json.dumps(data, ensure_ascii=False)

    # run generator
    try:
        result = subprocess.run(
            ["python3", GENERATOR, cfg["template"], svg_path, data_json],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.exists(svg_path):
            size = os.path.getsize(svg_path)
            print(f"  diagram: {svg_path} ({size // 1024}KB)")
            return svg_path
        else:
            print(f"  ⚠️  diagram failed: {result.stderr.strip() or result.stdout.strip()}")
            return None
    except Exception as e:
        print(f"  ⚠️  diagram error: {e}")
        return None


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/generate-diagram.py content/posts/{slug}.md {slug}")
        sys.exit(1)

    post_file = sys.argv[1]
    slug = sys.argv[2]

    if not os.path.exists(post_file):
        print(f"  ⚠️  post file not found: {post_file}")
        sys.exit(0)  # non-fatal

    generate_diagram(post_file, slug)


if __name__ == "__main__":
    main()
