#!/bin/bash
# Nebari terminal customization - Starship prompt init
# Sourced by /etc/profile.d/ on login shell startup.
# Controlled by NEBARI_TERMINAL_CUSTOMIZATION env var (set by KubeSpawner).

if [ "${NEBARI_TERMINAL_CUSTOMIZATION}" = "false" ]; then
    return 0 2>/dev/null || exit 0
fi

# Auto-detect JupyterLab theme. The nerd-font-loader.js writes "dark" or "light"
# to /tmp/.starship-theme. Use matching Catppuccin palette config.
# Users can override by setting STARSHIP_CONFIG in their .bashrc.
if [ -z "${STARSHIP_CONFIG}" ]; then
    if [ -f /tmp/.starship-theme ] && grep -q "light" /tmp/.starship-theme 2>/dev/null; then
        export STARSHIP_CONFIG="/etc/starship-light.toml"
    else
        export STARSHIP_CONFIG="/etc/starship.toml"
    fi
fi

if command -v starship &> /dev/null; then
    eval "$(starship init bash)"
    eval "$(starship completions bash)"
fi
