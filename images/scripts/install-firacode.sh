#!/usr/bin/env bash
# Install plain Fira Code (NOT the Nerd Font variant) for JupyterLab terminal.
# Just the two WOFF2 weights we actually use — Regular and Bold.
set -euo pipefail

FIRA_VERSION="${FIRA_VERSION:-6.2}"
JUPYTERLAB_STATIC_DIR="${1:?Usage: install-firacode.sh <jupyterlab-static-dir>}"
FONT_DIR="${JUPYTERLAB_STATIC_DIR}/fonts"

mkdir -p "${FONT_DIR}"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL -o "${TMP_DIR}/FiraCode.zip" \
    "https://github.com/tonsky/FiraCode/releases/download/${FIRA_VERSION}/Fira_Code_v${FIRA_VERSION}.zip"

unzip -q -j "${TMP_DIR}/FiraCode.zip" \
    'woff2/FiraCode-Regular.woff2' \
    'woff2/FiraCode-Bold.woff2' \
    -d "${FONT_DIR}"

echo "Fira Code installed to ${FONT_DIR}"
