# CodingFactory Lite — single image: backend + built frontend.
# Multi-stage: build the React app, then run everything in one non-root container.
# The tool plans and generates a build handoff prompt; it does not build/run code,
# so no coding CLI, git, or project workspaces are needed in the image.

FROM node:22-bookworm AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt

# Non-root user.
RUN useradd -m -u 10001 cf

COPY backend/ /app/backend
COPY --from=web /web/dist /app/frontend/dist
COPY scripts/docker_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data && chown -R cf:cf /app/data

USER cf
ENV PATH="/opt/venv/bin:$PATH"

ENV PYTHONPATH=/app/backend \
    DATA_DIR=/app/data

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
