#!/usr/bin/env bash
# Inject Nerd Font loading into JupyterLab's index.html.
set -euo pipefail

INDEX_HTML="${1:?Usage: inject-font.sh <path-to-index.html>}"
LOADER_JS="${2:?Usage: inject-font.sh <index.html> <nerd-font-loader.js>}"

python3 -c "
import sys

index_path = sys.argv[1]
js_path = sys.argv[2]

with open(js_path) as f:
    js_code = f.read().strip()

injection = (
    '<link rel=\"preload\" href=\"/static/lab/fonts/FiraCodeNerdFontMono-Regular.woff2\" as=\"font\" type=\"font/woff2\" crossorigin>'
    '<style>'
    '@font-face{font-family:\"FiraCode Nerd Font\";src:url(\"/static/lab/fonts/FiraCodeNerdFontMono-Regular.woff2\") format(\"woff2\");font-weight:400;font-display:block}'
    '@font-face{font-family:\"FiraCode Nerd Font\";src:url(\"/static/lab/fonts/FiraCodeNerdFontMono-Bold.woff2\") format(\"woff2\");font-weight:700;font-display:block}'
    '</style>'
    '<span style=\"font-family:FiraCode Nerd Font;position:absolute;left:-9999px;visibility:hidden\">\uf07c</span>'
    '<script>' + js_code + '</script>'
)

with open(index_path) as f:
    html = f.read()
html = html.replace('</head>', injection + '</head>', 1)
with open(index_path, 'w') as f:
    f.write(html)
print(f'Font injection complete: {index_path}')
" "$INDEX_HTML" "$LOADER_JS"
