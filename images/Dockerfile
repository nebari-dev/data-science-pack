ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE} AS builder
LABEL MAINTAINER="Nebari development team"

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    wget \
    bzip2 \
    ca-certificates \
    curl \
    git

COPY scripts /opt/scripts

RUN curl -fsSL https://pixi.sh/install.sh | PIXI_HOME=/usr/local bash

ENV PATH=/opt/scripts:${PATH}

ENV DEFAULT_ENV=default


# ========== jupyterhub install ===========
FROM builder AS jupyterhub

COPY --from=builder /usr/local/bin/pixi /usr/local/bin/pixi
COPY jupyterhub/pixi.toml /opt/jupyterhub/pixi.toml
COPY jupyterhub/pixi.lock /opt/jupyterhub/pixi.lock
RUN --mount=type=cache,target=/root/.cache/rattler/cache,sharing=locked \
    pixi install --manifest-path /opt/jupyterhub/ -e ${DEFAULT_ENV} --locked

COPY jupyterhub /opt/jupyterhub
RUN /opt/jupyterhub/postBuild

WORKDIR /srv/jupyterhub

# So we can actually write a db file here
RUN fix-permissions /srv/jupyterhub

ENV PATH=/opt/jupyterhub/.pixi/envs/${DEFAULT_ENV}/bin:${PATH}

CMD ["jupyterhub", "--config", "/usr/local/etc/jupyterhub/jupyterhub_config.py"]


# ========== jupyterlab base ===========
FROM builder AS intermediate
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN chmod -R a-w ~
ENV TZ=UTC
# Set timezone
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    locales \
    libnss-wrapper \
    xvfb


# ========== jupyterlab-base install ===========
FROM intermediate AS jupyterlab-base

ARG GPU

ENV CONDA_DIR=/opt/conda \
    DEFAULT_ENV=default \
    LD_LIBRARY_PATH=${GPU:+/usr/local/nvidia/lib64} \
    NVIDIA_PATH=${GPU:+/usr/local/nvidia/bin}

ENV PATH=${GPU:+/usr/local/nvidia/bin:}${PATH}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglx-mesa0 \
    libxrandr2 \
    libxss1 \
    libxcursor1 \
    libxcomposite1 \
    libasound2t64 \
    libxi6 \
    libxtst6 \
    libfontconfig1 \
    libxrender1 \
    libosmesa6

COPY --from=builder /usr/local/bin/pixi /usr/local/bin/pixi
COPY jupyterlab/pixi.toml /opt/jupyterlab/pixi.toml
COPY jupyterlab/pixi.lock /opt/jupyterlab/pixi.lock
RUN --mount=type=cache,target=/root/.cache/rattler/cache,sharing=locked \
    pixi install --manifest-path /opt/jupyterlab/ -e ${DEFAULT_ENV} --locked

# ========== code-server install ============
ENV PATH=/opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/share/code-server/bin:${PATH}

COPY jupyterlab /opt/jupyterlab
RUN /opt/jupyterlab/postBuild

ENV LD_LIBRARY_PATH=/usr/local/nvidia/lib64
ENV NVIDIA_PATH=/usr/local/nvidia/bin
ENV PATH=/opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/bin:${NVIDIA_PATH}:$PATH


# ========== nebi-source (resolve nebi binary) ===========
# When NEBI_IMAGE is set (e.g., quay.io/nebari/nebi:sha-abc1234), the binary is
# copied from that image. Otherwise, downloaded from the GitHub release.
# Useful for testing unreleased nebi versions.
ARG NEBI_IMAGE=""
FROM ${NEBI_IMAGE:-scratch} AS nebi-source

# ========== jupyterlab (base + nebi) ===========
FROM jupyterlab-base AS jupyterlab

ARG NEBI_VERSION=0.10.3
ARG TARGETARCH

# Install nebi: copy from NEBI_IMAGE if provided, else download from GitHub release.
RUN --mount=from=nebi-source,dst=/nebi-source \
    if [ -f /nebi-source/app/nebi ]; then \
        cp /nebi-source/app/nebi /usr/local/bin/nebi; \
    else \
        curl -fsSL \
        "https://github.com/nebari-dev/nebi/releases/download/v${NEBI_VERSION}/nebi_${NEBI_VERSION}_linux_$([ "${TARGETARCH}" = "amd64" ] && echo "x86_64" || echo "arm64").tar.gz" \
        | tar -xz -C /usr/local/bin nebi; \
    fi

# ========== terminal customization ===========
# Install Starship prompt (multi-arch: amd64/arm64)
ARG STARSHIP_VERSION=1.24.2
RUN curl -fsSL "https://github.com/starship/starship/releases/download/v${STARSHIP_VERSION}/starship-$(uname -m)-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin starship \
    && chmod +x /usr/local/bin/starship

# Install Fira Code Nerd Font (TTF for browser terminal rendering)
# unzip is provided by pixi; add pixi env to PATH for this step
RUN PATH="/opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/bin:${PATH}" \
    /opt/scripts/install-fonts.sh "/opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/share/jupyter/lab/static"

# Inject Nerd Font: @font-face in index.html + patch default terminal font
# in JupyterLab's built JS. The settingregistry has version mismatches that
# prevent font overrides from reaching xterm.js, so we patch the hardcoded
# defaultOptions directly in the bundle.
COPY terminal/nerd-font-loader.js /tmp/nerd-font-loader.js
RUN /opt/scripts/inject-font.sh \
    /opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/share/jupyter/lab/static/index.html \
    /tmp/nerd-font-loader.js \
    && rm /tmp/nerd-font-loader.js

# Patch JupyterLab's JS bundle:
# 1. Change default terminal font to Nerd Font
# 2. Gate xterm.open() on font availability (prevents boxes on first load)
RUN python3 /opt/scripts/patch-terminal-font.py \
    /opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/share/jupyter/lab/static/jlab_core.*.js

# Shell configuration
COPY terminal/bashrc /etc/skel/.bashrc
COPY terminal/nebari-terminal.sh /etc/profile.d/nebari-terminal.sh
COPY terminal/starship.toml /etc/starship.toml
COPY terminal/starship-light.toml /etc/starship-light.toml

# JupyterLab settings overrides (schema defaults for theme)
COPY terminal/overrides.json /opt/jupyterlab/.pixi/envs/${DEFAULT_ENV}/share/jupyter/lab/settings/overrides.json

# Terminal font as explicit user setting (NOT schema default).
# overrides.json only changes schema defaults, but JupyterLab's Terminal widget
# has hardcoded defaultOptions that take precedence over schema defaults.
# Writing to /etc/skel ensures new users get these as actual user settings.
RUN mkdir -p /etc/skel/.jupyter/lab/user-settings/@jupyterlab/terminal-extension
COPY terminal/terminal-settings.json /etc/skel/.jupyter/lab/user-settings/@jupyterlab/terminal-extension/plugin.jupyterlab-settings

COPY nebi/jupyter_server_config.py /usr/local/etc/jupyter/jupyter_server_config.py
COPY nebi/icons /usr/local/etc/jupyter/icons
