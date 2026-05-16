#!/usr/bin/env bash
# Install IBM Plex Sans woff2 files for JupyterLab UI/content font.
# Four faces: Regular, Bold, Italic, BoldItalic.
set -euo pipefail

PLEX_VERSION="${PLEX_VERSION:-1.1.0}"
JUPYTERLAB_STATIC_DIR="${1:?Usage: install-ibm-plex-sans.sh <jupyterlab-static-dir>}"
FONT_DIR="${JUPYTERLAB_STATIC_DIR}/fonts"

mkdir -p "${FONT_DIR}"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL -o "${TMP_DIR}/ibm-plex-sans.zip" \
    "https://github.com/IBM/plex/releases/download/%40ibm/plex-sans%40${PLEX_VERSION}/ibm-plex-sans.zip"

unzip -q -j "${TMP_DIR}/ibm-plex-sans.zip" \
    'ibm-plex-sans/fonts/complete/woff2/IBMPlexSans-Regular.woff2' \
    'ibm-plex-sans/fonts/complete/woff2/IBMPlexSans-Bold.woff2' \
    'ibm-plex-sans/fonts/complete/woff2/IBMPlexSans-Italic.woff2' \
    'ibm-plex-sans/fonts/complete/woff2/IBMPlexSans-BoldItalic.woff2' \
    -d "${FONT_DIR}"

echo "IBM Plex Sans installed to ${FONT_DIR}"
