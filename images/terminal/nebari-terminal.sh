#!/bin/bash
# Nebari terminal customization - aliases + Starship prompt init.
# Sourced by /etc/profile.d/ on login shell startup. Aliases live here
# (not in ~/.bashrc) because terminado runs bash as a login shell, and
# login shells don't source ~/.bashrc by default.

# Controlled by NEBARI_TERMINAL_CUSTOMIZATION env var (set by KubeSpawner).
if [ "${NEBARI_TERMINAL_CUSTOMIZATION}" = "false" ]; then
    return 0 2>/dev/null || exit 0
fi

# Colored ls / grep
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'

# Listing shortcuts
alias l='ls -lah'
alias ll='ls -lah'
alias la='ls -A'

# PREFERRED_USERNAME is set by the KubeSpawner pre_spawn_hook (see
# config/jupyterhub/01-spawner.py, matching classic nebari's
# get_username_hook) from the OIDC preferred_username claim. The
# starship prompt reads it via env_var. We keep it as-is — never strip
# the @domain, since users from different orgs may share a local-part.

# Use the system starship.toml unless the user explicitly overrides.
if [ -z "${STARSHIP_CONFIG}" ]; then
    export STARSHIP_CONFIG="/etc/starship.toml"
fi

if command -v starship &> /dev/null; then
    eval "$(starship init bash)"
    eval "$(starship completions bash)"
fi
