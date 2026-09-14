#!/usr/bin/env bash
# check-links.sh - Internal link checker for the Astro/Starlight docs site.
# Verifies every internal href/src in docs/dist/**/*.html resolves to an
# existing file under dist/. Derives the sub-path prefix from BASE (default "/").
# Exits 0 (LINKS_OK) on success; exits 1 and lists offenders on failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$REPO_ROOT/docs/dist"

# Build unless SKIP_BUILD=1 (CI sets SKIP_BUILD after its own build step so we
# validate the exact deployed artifact).
if [ -n "${SKIP_BUILD:-}" ]; then
    [ -d "$DIST_DIR" ] || { echo "ERROR: SKIP_BUILD set but $DIST_DIR missing - build first." >&2; exit 1; }
    echo "SKIP_BUILD set - checking existing $DIST_DIR."
else
    echo "Building site..."
    (cd "$REPO_ROOT/docs" && npm run build)
fi

# Sub-path prefix from BASE: "/data-science-pack/" -> "/data-science-pack"; "/" -> "".
BASE="${BASE:-/}"
SUBPATH_PREFIX="$(printf '%s' "$BASE" | sed 's:/*$::')"

resolve_path() {
    local href="$1"
    if [ -n "$SUBPATH_PREFIX" ]; then
        href="${href#"$SUBPATH_PREFIX"}"
    fi
    [ -z "$href" ] && href="/"
    if [[ "$href" == */ ]]; then
        echo "$DIST_DIR${href}index.html"
    else
        echo "$DIST_DIR$href"
    fi
}

BROKEN_LINKS=()
while IFS= read -r -d '' html_file; do
    while IFS= read -r url; do
        [ -z "$url" ] && continue
        case "$url" in
            http://*|https://*|//*) continue ;;   # external / protocol-relative
            \#*|mailto:*|data:*) continue ;;       # anchors / mailto / data URIs
            /*) ;;                                  # only absolute paths
            *) continue ;;
        esac
        url="${url%%#*}"                            # drop #fragment
        [ -z "$url" ] && continue
        target="$(resolve_path "$url")"
        [ -e "$target" ] || BROKEN_LINKS+=("BROKEN: $html_file -> $url (resolved: $target)")
    done < <(
        grep -oE 'href="[^"]*"|src="[^"]*"' "$html_file" \
            | sed -E 's/^(href|src)="//; s/"$//'
    )
done < <(find "$DIST_DIR" -name "*.html" -print0)

if [ ${#BROKEN_LINKS[@]} -gt 0 ]; then
    echo ""
    echo "Internal link failures (${#BROKEN_LINKS[@]}):"
    printf '  %s\n' "${BROKEN_LINKS[@]}"
    exit 1
fi
echo "LINKS_OK"
