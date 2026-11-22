#!/bin/sh
# Generate an OpenCode provider config so the coding agent uses the SAME
# single configured LLM as the agents (one model, principle #1), then start.
set -eu
CONF_DIR="${HOME}/.config/opencode"
mkdir -p "$CONF_DIR"
MODEL_NAME="${LLM_MODEL:-qwen3.8-flash-next-iq3_s}"
cat > "$CONF_DIR/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "codingfactory": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "CodingFactory LLM",
      "options": {
        "baseURL": "${LLM_BASE_URL:-http://lxc-strata2:8080/v1}",
        "apiKey": "${LLM_API_KEY:-}"
      },
      "models": { "\${MODEL_NAME}": { "name": "CodingFactory Model" } }
    }
  },
  "model": "codingfactory/\${MODEL_NAME}"
}
EOF
export OPENCODE_MODEL="codingfactory/${MODEL_NAME}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir /app/backend
