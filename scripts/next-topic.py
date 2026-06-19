#!/usr/bin/env python3
"""
确定下一篇要写的系列文章。
输出 JSON: title, keywords, difficulty, target_length, slug, series_name, series_number, series_total

用法: python3 scripts/next-topic.py
"""
import json, os, re, sys

LIBRARY = "/Users/dudu/workspace-daliy/副业探索/自动化推文流水线/选题库-系列制.md"
POSTS_DIR = "/Users/dudu/cursor-workspace/blog-repo/content/posts"

SERIES_SLUG_PREFIX = {
    '面向面试之 Redis':              'redis',
    '线上问题排查':                  'troubleshoot',
    '面向面试之 JVM':                'jvm',
    '面向面试之 并发编程':            'concurrency',
    '面向面试之 数据库':              'database',
    'Spring Boot 生产实战':          'springboot',
    'Kafka 实战':                    'kafka',
    'Spring Boot':                   'springboot',
    '数据库':                        'database',
}

def get_existing():
    """Return set of (series_name, number) already written."""
    written = set()
    if not os.path.isdir(POSTS_DIR):
        return written
    for fname in os.listdir(POSTS_DIR):
        if not fname.endswith('.md'):
            continue
        text = open(os.path.join(POSTS_DIR, fname), encoding='utf-8').read()
        sn = re.search(r'series_name\s*=\s*[\'"]([^\'"]+)[\'"]', text)
        no = re.search(r"series_number\s*=\s*(\d+)", text)
        if sn and no:
            written.add((sn.group(1), int(no.group(1))))
    return written

def extract_keyword(title):
    """从标题提取短关键词给 slug（5-10 字，取主题关键词）。"""
    # Remove series prefix: "面向面试之 Redis 系列七（番外）：" or similar
    t = re.sub(r'^.*?系列[N\d一二三四五六七八九十]+（番外）[：:]', '', title)
    t = re.sub(r'^.*?系列[N\d一二三四五六七八九十]+[：:]', '', t)
    # Remove subtitle after ——
    t = re.sub(r'[——\-–——].*$', '', t)
    # Remove brackets content
    t = re.sub(r'[（(][^）)]*[）)]', '', t)
    t = t.strip()
    # Keep only Chinese chars, max 8
    t = re.sub(r'[^\u4e00-\u9fff]', '', t)
    return t[:8]

def make_slug(series_name, num, title):
    prefix = SERIES_SLUG_PREFIX.get(series_name, 'post')
    kw = extract_keyword(title)
    return f'{prefix}-{num}-{kw}'

def main():
    if not os.path.exists(LIBRARY):
        print(json.dumps({'error': f'Library not found: {LIBRARY}'}))
        sys.exit(1)

    text = open(LIBRARY, encoding='utf-8').read()
    written = get_existing()

    # Parse all sections: ### ... P\d · SeriesName（N篇）
    # Then capture table rows until next ### or ---
    sections = re.split(r'\n(?=###)', text)
    
    # Priority order from P0 to P3
    priority_order = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
    
    candidates = []
    series_order = 0  # tracks order of series appearance in library
    
    for section in sections:
        if not section.strip().startswith('###'):
            continue
        
        # Extract priority and series name from header
        hm = re.search(r'###\s*[⭐🥇🥈🥉]?\s*(P\d)\s*[·.\.]\s*(.+?)（(\d+)\s*篇）', section)
        if not hm:
            continue
        priority = hm.group(1)
        series_fullname = hm.group(2).strip()
        series_total = int(hm.group(3))
        # 番外篇：如果系列有番外，total 要包含它们
        if 'Redis' in series_fullname:
            series_total = 8  # 正片 6 + 番外 2
        series_order += 1  # each section = one series
        
        # Clean series name: remove leading "面向面试之 " etc
        series_name = series_fullname
        if '面试' in series_name:
            series_name = re.sub(r'^面向面试之\s*', '', series_name)
        
        # Find series_key from map — flexible matching
        series_key = None
        for k in SERIES_SLUG_PREFIX:
            if k in series_fullname or series_fullname.startswith(k) or k.split('之')[-1].strip() in series_fullname:
                series_key = k
                break
        if not series_key:
            continue
        
        # Parse table rows
        rows = re.findall(r'^\|\s*\|?\s*(\d+)\s*[🏅]?\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)', section, re.MULTILINE)
        
        for row in rows:
            num = int(row[0])
            title = row[1].strip()
            keywords = row[2].strip()
            difficulty = row[3].strip()
            target_length = int(row[4])
            
            # Skip if written
            if (series_key, num) in written:
                continue
            
            slug = make_slug(series_key, num, title)
            
            candidates.append({
                'priority_rank': priority_order.get(priority, 99),
                'priority': priority,
                'series_order': series_order,
                'series_name': series_key,
                'series_number': num,
                'series_total': series_total,
                'title': title,
                'keywords': keywords,
                'difficulty': difficulty,
                'target_length': target_length,
                'slug': slug,
            })
    
    if not candidates:
        print(json.dumps({'done': True, 'message': '所有系列文章已完成'}))
        return
    
    # Sort: by series order in library, then by number within series
    # This ensures all articles in a series (including 番外) are done before next series
    candidates.sort(key=lambda c: (c['series_order'], c['series_number']))
    
    result = candidates[0]
    # Remove internal fields
    del result['priority_rank']
    del result['series_order']
    
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
