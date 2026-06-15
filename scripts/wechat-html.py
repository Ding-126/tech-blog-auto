#!/usr/bin/env python3
"""Convert markdown article to WeChat-friendly HTML."""
import re, sys, os

def convert(body_file, title, source_url):
    with open(body_file, encoding='utf-8') as f:
        text = f.read()

    # Remove front matter
    text = re.sub(r'^\+\+\+\n.*?\+\+\+\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^---\n.*?---\n', '', text, flags=re.DOTALL)

    lines = text.split('\n')
    parts = []
    in_code = False
    buf = []

    for line in lines:
        s = line.strip()
        if s.startswith('```'):
            if in_code:
                parts.append('<pre><code>' + '\n'.join(buf) + '</code></pre>')
                buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            buf.append(line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            continue
        if s.startswith('## '):
            parts.append(f'<h2 style="font-size:17px;margin:18px 0 8px">{s[3:]}</h2>')
        elif s.startswith('### '):
            parts.append(f'<h3 style="font-size:15px;margin:14px 0 6px">{s[4:]}</h3>')
        elif s == '---':
            parts.append('<hr style="border:none;border-top:1px solid #ddd;margin:16px 0"/>')
        elif s.startswith('- ') or s.startswith('* '):
            parts.append(f'<p style="margin:4px 0">\u2022 {s[2:]}</p>')
        elif s.startswith('> '):
            parts.append(f'<blockquote style="border-left:3px solid #22d3ee;padding-left:12px;margin:10px 0;color:#666"><p style="margin:0">{s[2:]}</p></blockquote>')
        elif s.startswith('|'):
            parts.append(f'<p style="margin:4px 0;font-size:13px">{s}</p>')
        elif s:
            cv = re.sub(r'`(.+?)`', r'<code style="background:#f5f5f5;padding:2px 4px;border-radius:3px;font-size:13px">\1</code>', line)
            cv = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', cv)
            parts.append(f'<p style="margin:8px 0">{cv}</p>')

    body = '\n'.join(parts)
    html = f'''<section style="font-size:15px;line-height:1.8;color:#333;padding:0 10px;max-width:677px">
{body}
<hr style="border:none;border-top:1px solid #ddd;margin:20px 0"/>
<p style="color:#999;font-size:13px">原文：<a href="{source_url}" style="color:#22d3ee">{source_url}</a></p>
</section>'''
    return html


if __name__ == '__main__':
    body_file = sys.argv[1]
    html_out = sys.argv[2]
    title = os.environ.get('TITLE', '')
    source_url = os.environ.get('SOURCE_URL', '')
    html = convert(body_file, title, source_url)
    with open(html_out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'WeChat HTML: {html_out}')
