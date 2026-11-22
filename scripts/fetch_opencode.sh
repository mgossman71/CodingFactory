#!/bin/bash
# Fetch the pinned OpenCode v2 CLI tarball for the Docker build.
# Architecture-aware: arm64 on Apple Silicon, x64 on typical Ubuntu servers.
set -eu
cd "$(dirname "$0")/.."
VERSION=v2.3.2
case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64 ;;
  *) ARCH=x64 ;;
esac
mkdir -p .docker-assets
URL="https://github.com/anomalyco/opencode/releases/download/${VERSION}/opencode-linux-${ARCH}.tar.gz"
echo "fetching $URL"
curl -fsSL "$URL" -o .docker-assets/opencode-oc.tar.gz
echo "ok: $(du -h .docker-assets/opencode-oc.tar.gz | cut -f1)"
