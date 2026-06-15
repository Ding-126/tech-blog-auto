#!/usr/bin/env python3
"""博客发文台账：不依赖挪文件也能看清 draft / approved / published。"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "data" / "blog-queue.json"
PUBLISHED_FILE = ROOT / "data" / "published-urls.json"
POSTS_DIR = ROOT / "content" / "posts"
DRAFTS_DIR = ROOT / "content" / "drafts"
QUEUE_DIR = ROOT / "content" / "queue"
ARCHIVE_DIR = ROOT / "content" / "_archive"

STATUSES = ("draft", "approved", "published", "rejected")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8") or json.dumps(default))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_queue() -> dict:
    data = load_json(QUEUE_FILE, {"version": 1, "items": []})
    if "items" not in data:
        data["items"] = []
    return data


def save_queue(data: dict) -> None:
    save_json(QUEUE_FILE, data)


def parse_front_matter(text: str) -> dict:
    m = re.search(r"^\+\+\+(.*)^\+\+\+", text, re.S | re.M)
    if not m:
        return {}
    block = m.group(1)
    out = {}
    for key in ("title", "source_url", "source_name", "date"):
        km = re.search(rf"^{key}\s*=\s*['\"]([^'\"]+)['\"]", block, re.M)
        if km:
            out[key] = km.group(1).strip()
    dm = re.search(r"^draft\s*=\s*(true|false)", block, re.M)
    if dm:
        out["draft"] = dm.group(1) == "true"
    return out


def slug_from_path(path: Path) -> str:
    return path.stem


def find_item(items: list, slug: str | None = None, source_url: str | None = None):
    for it in items:
        if slug and it.get("slug") == slug:
            return it
        if source_url and it.get("source_url") == source_url:
            return it
    return None


def upsert_item(items: list, entry: dict) -> dict:
    it = find_item(items, slug=entry.get("slug"), source_url=entry.get("source_url"))
    if it:
        it.update({k: v for k, v in entry.items() if v is not None})
        return it
    items.append(entry)
    return entry


def sync_from_disk() -> dict:
    """根据磁盘文件与 published-urls.json 对齐台账。"""
    queue = load_queue()
    items = queue["items"]
    published_records = load_json(PUBLISHED_FILE, [])

    published_by_post = {r.get("post"): r for r in published_records if r.get("post")}
    published_urls = {r.get("url") for r in published_records if r.get("url")}

    seen_slugs: set[str] = set()

    def touch(path: Path, status: str) -> None:
        if not path.exists():
            return
        slug = slug_from_path(path)
        seen_slugs.add(slug)
        meta = parse_front_matter(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(ROOT))
        entry = {
            "slug": slug,
            "title": meta.get("title", slug),
            "source_url": meta.get("source_url", ""),
            "status": status,
            "paths": {
                "post": rel if status == "published" else None,
                "draft": rel if status == "draft" else None,
                "queue": rel if status == "approved" else None,
            },
            "updated_at": now_iso(),
        }
        if status == "published":
            rec = published_by_post.get(rel)
            if rec:
                entry["published_at"] = rec.get("published_at")
        upsert_item(items, entry)

    for p in sorted(POSTS_DIR.glob("*.md")):
        rel = str(p.relative_to(ROOT))
        slug = slug_from_path(p)
        if rel in published_by_post:
            touch(p, "published")
            continue
        existing = find_item(items, slug=slug)
        if existing and existing.get("status") == "scheduled":
            touch(p, "scheduled")
        else:
            touch(p, "published")

    for p in sorted(QUEUE_DIR.glob("*.md")):
        touch(p, "approved")

    for p in sorted(DRAFTS_DIR.glob("*.md")):
        touch(p, "draft")

    for p in sorted(ARCHIVE_DIR.glob("*.md")):
        slug = slug_from_path(p)
        seen_slugs.add(slug)
        meta = parse_front_matter(p.read_text(encoding="utf-8"))
        upsert_item(
            items,
            {
                "slug": slug,
                "title": meta.get("title", slug),
                "source_url": meta.get("source_url", ""),
                "status": "published",
                "paths": {"post": f"content/posts/{slug}.md", "archive": str(p.relative_to(ROOT))},
                "updated_at": now_iso(),
            },
        )

    # published-urls 里有但 posts 目录也有的，确保 status=published
    for rec in published_records:
        post = rec.get("post")
        if not post:
            continue
        slug = Path(post).stem
        it = find_item(items, slug=slug)
        if it:
            it["status"] = "published"
            it["source_url"] = rec.get("url") or it.get("source_url", "")
            it["title"] = rec.get("title") or it.get("title", slug)
            it["published_at"] = rec.get("published_at")
            it.setdefault("paths", {})["post"] = post
        else:
            items.append(
                {
                    "slug": slug,
                    "title": rec.get("title", slug),
                    "source_url": rec.get("url", ""),
                    "status": "published",
                    "paths": {"post": post},
                    "published_at": rec.get("published_at"),
                    "updated_at": now_iso(),
                }
            )

    # 标记孤立台账：文件已不存在且非 published
    for it in items:
        slug = it.get("slug", "")
        status = it.get("status")
        paths = it.get("paths") or {}
        if status == "published":
            post_path = paths.get("post")
            if post_path and not (ROOT / post_path).exists():
                if slug not in seen_slugs:
                    it["status"] = "orphan"
            continue
        for key in ("draft", "queue"):
            rel = paths.get(key)
            if rel and not (ROOT / rel).exists():
                paths[key] = None
        has_file = any(
            (ROOT / paths[k]).exists() for k in ("draft", "queue", "post") if paths.get(k)
        )
        if not has_file and status in ("draft", "approved"):
            it["status"] = "orphan"

    queue["items"] = sorted(items, key=lambda x: (x.get("status", ""), x.get("slug", "")))
    queue["synced_at"] = now_iso()
    save_queue(queue)
    return queue


def cmd_status(_: argparse.Namespace) -> int:
    queue = sync_from_disk()
    items = queue["items"]
    groups = {s: [] for s in (*STATUSES, "orphan")}
    for it in items:
        groups.setdefault(it.get("status", "unknown"), []).append(it)

    print("=== 博客发文台账（blog-queue.json）===")
    print(f"同步时间: {queue.get('synced_at', '-')}\n")

    labels = {
        "draft": "草稿（待你审核）",
        "approved": "待发布队列（已批准）",
        "scheduled": "已写入 posts/，待 git push",
        "published": "已发布（已 push + 去重清单）",
        "rejected": "已拒绝",
        "orphan": "孤立记录（文件已删）",
    }
    for status, label in labels.items():
        rows = groups.get(status, [])
        print(f"【{label}】 {len(rows)} 篇")
        if not rows:
            print("  （无）\n")
            continue
        for it in rows:
            title = (it.get("title") or it.get("slug", ""))[:40]
            src = (it.get("source_url") or "-")[:50]
            paths = it.get("paths") or {}
            loc = paths.get("post") or paths.get("queue") or paths.get("draft") or "-"
            pub = it.get("published_at", "")
            pub_s = f" | 发布于 {pub[:16]}" if pub else ""
            print(f"  • {it.get('slug')}: {title}")
            print(f"    文件: {loc} | 来源: {src}{pub_s}")
        print()

    pub_urls = load_json(PUBLISHED_FILE, [])
    print(f"=== 已发去重清单（published-urls.json）: {len(pub_urls)} 条 ===")
    for rec in pub_urls[-5:]:
        print(f"  • {rec.get('title', '')[:35]} → {rec.get('url', '')[:55]}")
    if len(pub_urls) > 5:
        print(f"  … 共 {len(pub_urls)} 条，完整列表见 data/published-urls.json")

    print("\n提示: 已发布文章只在 content/posts/；草稿在 content/drafts/；待发表在 content/queue/")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    sync_from_disk()
    queue = load_queue()
    src = Path(args.file)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"错误: 文件不存在 {src}", file=sys.stderr)
        return 1

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    slug = slug_from_path(src)
    dest = QUEUE_DIR / f"{slug}.md"
    if src.parent.resolve() != DRAFTS_DIR.resolve():
        print(f"错误: 仅支持从 content/drafts/ 批准，当前: {src}", file=sys.stderr)
        return 1

    text = src.read_text(encoding="utf-8")
    if "draft = true" not in text and "draft=true" not in text:
        text = text.replace("draft = false", "draft = true", 1)
    dest.write_text(text, encoding="utf-8")
    src.unlink()

    meta = parse_front_matter(text)
    upsert_item(
        queue["items"],
        {
            "slug": slug,
            "title": meta.get("title", slug),
            "source_url": meta.get("source_url", ""),
            "status": "approved",
            "paths": {"draft": None, "queue": str(dest.relative_to(ROOT)), "post": None},
            "approved_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    save_queue(queue)
    print(f"已批准进待发布队列: {dest.relative_to(ROOT)}")
    return 0


def cmd_publish_next(args: argparse.Namespace) -> int:
    sync_from_disk()
    queue = load_queue()
    published_urls = {r.get("url") for r in load_json(PUBLISHED_FILE, []) if r.get("url")}

    candidates = [
        it
        for it in queue["items"]
        if it.get("status") == "approved"
        and (ROOT / (it.get("paths") or {}).get("queue", "")).exists()
        if (it.get("paths") or {}).get("queue")
    ]
    candidates.sort(key=lambda x: x.get("approved_at") or x.get("updated_at") or "")

    if not candidates:
        print("待发布队列为空。先 approve 草稿，或运行 blog-status 查看。")
        return 0

    it = candidates[0]
    paths = it.get("paths") or {}
    queue_path = ROOT / paths["queue"]
    slug = it["slug"]
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    text = queue_path.read_text(encoding="utf-8")
    text = re.sub(r"^draft\s*=\s*true", "draft = false", text, count=1, flags=re.M)
    if "draft = false" not in text:
        text = text.replace("+++\n", "+++\ndraft = false\n", 1)

    post_path = POSTS_DIR / f"{slug}.md"
    post_path.write_text(text, encoding="utf-8")

    archive_path = ARCHIVE_DIR / f"{slug}.md"
    shutil.copy2(queue_path, archive_path)
    queue_path.unlink()

    rel_post = str(post_path.relative_to(ROOT))
    it["status"] = "scheduled"
    it["paths"] = {
        "post": rel_post,
        "queue": None,
        "archive": str(archive_path.relative_to(ROOT)),
    }
    it["scheduled_at"] = now_iso()
    it["updated_at"] = now_iso()
    save_queue(queue)

    if args.quiet:
        print(rel_post)
    else:
        print(f"已从队列写入: {rel_post}")
        print(f"备份: {archive_path.relative_to(ROOT)}")
        print(f"下一步: ./scripts/publish-post.sh {rel_post}")
    return 0


def cmd_register_published(args: argparse.Namespace) -> int:
    """publish-post.sh 发布后回写台账。"""
    post = Path(args.file)
    if not post.is_absolute():
        post = ROOT / post
    slug = slug_from_path(post)
    meta = parse_front_matter(post.read_text(encoding="utf-8"))
    queue = load_queue()
    upsert_item(
        queue["items"],
        {
            "slug": slug,
            "title": meta.get("title", slug),
            "source_url": meta.get("source_url", ""),
            "status": "published",
            "paths": {"post": str(post.relative_to(ROOT)), "draft": None, "queue": None},
            "published_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    save_queue(queue)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="博客发文台账")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="同步并打印全文状态表")
    p_status.set_defaults(func=cmd_status)

    p_sync = sub.add_parser("sync", help="仅同步磁盘与 JSON")
    p_sync.set_defaults(func=lambda _: (sync_from_disk(), print("已同步 blog-queue.json"), 0)[2])

    p_approve = sub.add_parser("approve", help="草稿 → 待发布队列")
    p_approve.add_argument("file", help="content/drafts/xxx.md")
    p_approve.set_defaults(func=cmd_approve)

    p_pub = sub.add_parser("publish-next", help="从队列取 1 篇写入 content/posts/")
    p_pub.add_argument("-q", "--quiet", action="store_true", help="仅输出 posts 路径")
    p_pub.set_defaults(func=cmd_publish_next)

    p_reg = sub.add_parser("register-published", help="发布后登记台账")
    p_reg.add_argument("file", help="content/posts/xxx.md")
    p_reg.set_defaults(func=cmd_register_published)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
