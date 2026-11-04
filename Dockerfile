# syntax=docker/dockerfile:1
# Atlas in a container (docs/ARCHITECTURE.md, Container): an empty container plus an API key gives a
# working conversation.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

# ripgrep: find_files searches with it (atlas.tools.find).
# tini: PID 1, reaping the orphans of run_command and of MCP servers, which would otherwise pile
# up as zombies under a Python PID 1 (Atlas V3 ran with `init: true`).
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ripgrep tini >/dev/null \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a code change does not reinstall them. The image brings every extra.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project

COPY README.md LICENSE ./
COPY LICENSES ./LICENSES
COPY src ./src
# The plugins Atlas makes (step 26), installed into a home from here.
COPY plugins ./plugins
RUN uv sync --frozen --no-dev --all-extras

# Atlas never runs as root, and the code in /app is root's. User state lives in the /data volume,
# never in the image; the user's home folder too (/data/home, where the workspace starts), so what
# the agent makes outlives a new image. A new named volume starts as a copy of /data here.
RUN useradd --uid 10001 --no-create-home --home-dir /data/home atlas \
    && mkdir -p /data/home && chown -R atlas /data
USER atlas
# 0.0.0.0 is the container's own interface; compose publishes it on the host's loopback only.
# Off loopback the server token is the one the vault keeps: `atlas server token` prints it.
# Logs go to stderr only, which is what `docker logs` collects.
ENV ATLAS_HOME=/data \
    HOME=/data/home \
    ATLAS_SERVER_HOST=0.0.0.0 \
    ATLAS_SERVER_PORT=18790 \
    ATLAS_LOG_FILE=false \
    PATH="/app/.venv/bin:${PATH}"
VOLUME ["/data"]
WORKDIR /data/home

EXPOSE 18790
# While it starts, checked every second, so Docker calls it healthy within seconds, not 30.
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=1s \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:18790/health').status != 200)"]

# -g: a stop reaches the whole process group, so the commands the agent runs stop with Atlas.
ENTRYPOINT ["tini", "-g", "--"]
CMD ["atlas", "serve"]
