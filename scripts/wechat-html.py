#!/usr/bin/env python3
"""Convert markdown article to WeChat-compatible HTML.

WeChat editor strips most HTML tags. What works:
- <section> with inline styles
- <p> with inline styles (font-size, color, background, padding, margin)
- <strong>/<b> for bold
- <span> with inline styles
- <img>
- <br/>

What does NOT work: <h1-6>, <pre>, <code>, <blockquote>, <table>, <hr>
"""
import re, sys, os


def convert_markdown_inline(text):
    """Convert **bold** and `inline code` markers to HTML."""
    # Bold: **text** -> <strong>text</strong>
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Inline code: `text` -> WeChat doesn't support <code>, use styled span
    text = re.sub(r'`(.+?)`', r'<span style="background:#f0f0f0;padding:0 4px;border-radius:3px;font-family:monospace;font-size:13px">\1</span>', text)
    return text


def convert(body_file, html_out):
    with open(body_file, encoding='utf-8') as f:
        text = f.read()

    # Remove front matter (between +++ or ---)
    text = re.sub(r'^\+\+\+\n.*?\+\+\+\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^---\n.*?---\n', '', text, flags=re.DOTALL)

    lines = text.split('\n')
    parts = []
    in_code = False
    code_buf = []

    for line in lines:
        s = line.strip()

        # Code blocks
        if s.startswith('```'):
            if in_code:
                # End code block: wrap as styled paragraph
                code_text = '\n'.join(code_buf)
                # Escape HTML entities
                code_text = code_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                parts.append(
                    f'<p style="background:#f5f5f5;font-family:monospace;font-size:13px;'
                    f'line-height:1.5;padding:12px;border-radius:6px;'
                    f'white-space:pre-wrap;overflow-x:auto;margin:8px 0">{code_text}</p>'
                )
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_buf.append(line)
            continue

        # Skip empty lines (but use them as spacing)
        if not s:
            parts.append('<p style="margin:4px 0"></p>')
            continue

        # Skip standalone separators
        if s == '---':
            parts.append('<p style="margin:12px 0;border-bottom:1px solid #ddd"></p>')
            continue

        # Headings: WeChat strips <h2>/<h3>, use styled <p> instead
        if s.startswith('## '):
            parts.append(
                f'<p style="font-weight:bold;font-size:17px;margin:18px 0 6px">'
                f'{convert_markdown_inline(s[3:])}</p>'
            )
            continue
        if s.startswith('### '):
            parts.append(
                f'<p style="font-weight:bold;font-size:15px;margin:14px 0 4px">'
                f'{convert_markdown_inline(s[4:])}</p>'
            )
            continue

        # Blockquote
        if s.startswith('> '):
            parts.append(
                f'<p style="border-left:3px solid #22d3ee;padding:6px 12px;'
                f'margin:8px 0;background:#f8fafc;font-size:14px;color:#555">'
                f'{convert_markdown_inline(s[2:])}</p>'
            )
            continue

        # List items: WeChat doesn't support <ul>/<li>, use <p> with bullet
        if s.startswith('- ') or s.startswith('* '):
            content = convert_markdown_inline(s[2:])
            parts.append(f'<p style="margin:4px 0;padding-left:12px">\u2022 {content}</p>')
            continue

        # Numbered list items
        if re.match(r'^\d+[\.\、]', s):
            parts.append(f'<p style="margin:4px 0">{convert_markdown_inline(s)}</p>')
            continue

        # Tables (WeChat strips table tags, just show as plain text)
        if s.startswith('|') and s.endswith('|'):
            parts.append(f'<p style="margin:2px 0;font-size:13px;font-family:monospace">{s}</p>')
            continue

        # Normal paragraph - apply inline markdown conversion
        parts.append(f'<p style="margin:8px 0;font-size:15px;line-height:1.8">{convert_markdown_inline(line)}</p>')

    body = '\n'.join(parts)
    
    html = f'''<section style="font-size:15px;line-height:1.8;color:#333;padding:0 12px;max-width:677px;margin:0 auto">
{body}
</section>'''

    with open(html_out, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f'WeChat HTML: {html_out}')


if __name__ == '__main__':
    body_file = sys.argv[1]
    html_out = sys.argv[2]
    convert(body_file, html_out)
