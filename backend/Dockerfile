# CodingFactory Lite — single image: backend + built frontend + OpenCode executor.
# Multi-stage: build the React app, then run everything in one non-root container.

FROM node:22-bookworm AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# The coding executor. Pinned for reproducibility.
RUN npm install -g opencode-ai@2.3.2

# Generated-project toolchains (node above; python here) + non-root user.
RUN useradd -m -u 10001 cf
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/ /app/backend
COPY --from=web /web/dist /app/frontend/dist
COPY scripts/docker_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data /app/workspaces && chown cf:cf /app/data /app/workspaces

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend \
    DATA_DIR=/app/data \
    WORKSPACES_DIR=/app/workspaces

USER cf
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
