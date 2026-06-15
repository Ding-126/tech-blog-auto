#!/usr/bin/env python3
"""Convert article to plain text (strip language tags from code blocks)."""
import sys, re

text = open(sys.argv[1], encoding='utf-8').read()
text = re.sub(r'```\w+', '```', text)
with open(sys.argv[2], 'w', encoding='utf-8') as f:
    f.write(text)
print(f'Plain: {sys.argv[2]}')
