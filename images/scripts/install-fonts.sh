#!/usr/bin/env bash
# Download Fira Code Nerd Font and convert to WOFF2 for web use.
# WOFF2 is smaller (1.2MB vs 2.6MB) and has better browser support than TTF.
set -euo pipefail
set -x

NERD_FONTS_VERSION="v3.4.0"
JUPYTERLAB_STATIC_DIR="${1:?Usage: install-fonts.sh <jupyterlab-static-dir>}"
FONT_DIR="${JUPYTERLAB_STATIC_DIR}/fonts"

mkdir -p "${FONT_DIR}"

DOWNLOAD_URL="https://github.com/ryanoasis/nerd-fonts/releases/download/${NERD_FONTS_VERSION}/FiraCode.zip"

TMP_DIR=$(mktemp -d)
curl -fsSL -o "${TMP_DIR}/FiraCode.zip" "${DOWNLOAD_URL}"

unzip -q "${TMP_DIR}/FiraCode.zip" \
    'FiraCodeNerdFontMono-Regular.ttf' \
    'FiraCodeNerdFontMono-Bold.ttf' \
    -d "${TMP_DIR}"

# Convert TTF to WOFF2 using fonttools (available via pixi)
python3 -c "
from fontTools.ttLib import TTFont
for weight in ['Regular', 'Bold']:
    ttf = '${TMP_DIR}/FiraCodeNerdFontMono-{}.ttf'.format(weight)
    woff2 = '${FONT_DIR}/FiraCodeNerdFontMono-{}.woff2'.format(weight)
    f = TTFont(ttf)
    f.flavor = 'woff2'
    f.save(woff2)
    print(f'Converted {ttf} -> {woff2}')
"

# Keep TTF copies as system fonts for container-internal rendering
mkdir -p /usr/local/share/fonts/firacode-nerd
cp "${TMP_DIR}/FiraCodeNerdFontMono-Regular.ttf" /usr/local/share/fonts/firacode-nerd/
cp "${TMP_DIR}/FiraCodeNerdFontMono-Bold.ttf" /usr/local/share/fonts/firacode-nerd/
fc-cache -f /usr/local/share/fonts/firacode-nerd

rm -rf "${TMP_DIR}"
echo "Fira Code Nerd Font installed to ${FONT_DIR}"
