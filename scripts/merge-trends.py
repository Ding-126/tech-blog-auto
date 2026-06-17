#!/usr/bin/env python3
"""Merge existing trends data with fresh HN data."""
import json
import pathlib

root = pathlib.Path('data')
merged = []

# Load existing data, remove old HN entries
existing = json.loads((root / 'trends-merged.json').read_text())
merged = [item for item in existing if item.get('source') not in ('hn',)]

# Add fresh HN data
hn_fresh = [
    {"source": "hn", "id": 48533848, "title": "Your ePub Is fine", "url": "https://andreklein.net/your-epub-is-fine-kobo-disagrees-blame-adobe/", "score": 598, "summary": ""},
    {"source": "hn", "id": 48536776, "title": "Apple Foundation Models", "url": "https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/apple-foundation-models", "score": 139, "summary": ""},
    {"source": "hn", "id": 48535886, "title": "Even more batteries included with Emacs", "url": "https://karthinks.com/software/even-more-batteries-included-with-emacs/", "score": 200, "summary": ""},
    {"source": "hn", "id": 48537165, "title": "Curl will not accept vulnerability reports during July 2026", "url": "https://daniel.haxx.se/blog/2026/06/15/curl-summer-of-bliss/", "score": 401, "summary": ""},
    {"source": "hn", "id": 48529990, "title": "Show HN: Kage - Shadow any website to a single binary for offline viewing", "url": "https://github.com/tamnd/kage", "score": 564, "summary": ""},
    {"source": "hn", "id": 48528371, "title": "Rio de Janeiro's homegrown LLM appears to be a merge of an existing model", "url": "https://github.com/nex-agi/Nex-N2/issues/4", "score": 353, "summary": ""},
    {"source": "hn", "id": 48521236, "title": "Show HN: Trace - Offline Mac meeting transcripts you can flag mid-call", "url": "https://traceapp.info", "score": 163, "summary": ""},
    {"source": "hn", "id": 48526633, "title": "Formal methods and the future of programming", "url": "https://blog.janestreet.com/formal-methods-at-jane-street-index/", "score": 273, "summary": ""},
    {"source": "hn", "id": 48531449, "title": "Chaosnet (1981)", "url": "https://tumbleweed.nu/r/lm-3/uv/amber.html", "score": 88, "summary": ""},
    {"source": "hn", "id": 48538229, "title": "What the Fuck Happened to Nerds", "url": "https://mrmarket.lol/what-the-fuck-happened-to-nerds/", "score": 128, "summary": ""},
]
merged.extend(hn_fresh)

# Apply source boosts
SOURCE_BOOST = {'juejin': 1.2, '36kr': 1.0, 'hn': 1.0, 'github': 0.8}
for item in merged:
    src = item.get('source', '')
    boost = SOURCE_BOOST.get(src, 1.0)
    item['score'] = int(item.get('score', 0) * boost)

merged.sort(key=lambda x: x.get('score', 0), reverse=True)
(root / 'trends-merged.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
print(f'Merged {len(merged)} items -> data/trends-merged.json')
