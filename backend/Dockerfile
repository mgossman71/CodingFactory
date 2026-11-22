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

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt

# Non-root user for everything that runs.
RUN useradd -m -u 10001 cf

# The coding executor: v2 CLI from GitHub release binaries (the opencode.ai
# installer only ships the v1 line). Pinned to the version we verified.
USER cf
RUN curl -fsSL https://github.com/anomalyco/opencode/releases/download/v2.3.2/opencode-linux-x64.tar.gz -o /tmp/oc.tgz \
    && mkdir -p /tmp/oc /home/cf/.opencode/bin \
    && tar -xzf /tmp/oc.tgz -C /tmp/oc \
    && install -m755 "$(find /tmp/oc -type f -name opencode | head -1)" /home/cf/.opencode/bin/opencode \
    && rm -rf /tmp/oc /tmp/oc.tgz
ENV PATH="/opt/venv/bin:/home/cf/.opencode/bin:$PATH"

COPY backend/ /app/backend
COPY --from=web /web/dist /app/frontend/dist
COPY scripts/docker_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data /app/workspaces && chown cf:cf /app/data /app/workspaces

ENV PYTHONPATH=/app/backend \
    DATA_DIR=/app/data \
    WORKSPACES_DIR=/app/workspaces

USER cf
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
