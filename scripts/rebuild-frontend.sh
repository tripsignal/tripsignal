#!/usr/bin/env bash
# Rebuild and redeploy the Trip Signal frontend container.
# Usage:
#   ./rebuild-frontend.sh              — rebuild production
#   ./rebuild-frontend.sh staging      — rebuild staging

set -euo pipefail

cd /opt/tripsignal/frontend

TARGET="${1:-production}"

if [[ "$TARGET" == "staging" ]]; then
  IMAGE="tripsignal-frontend-staging"
  CONTAINER="tripsignal-frontend-staging"
  PORT="3001"
  BRANCH="staging"
else
  IMAGE="tripsignal-frontend"
  CONTAINER="tripsignal-frontend"
  PORT="3000"
  BRANCH="main"
fi

echo "=== Rebuilding Trip Signal frontend ($TARGET) ==="

# Ensure correct branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "WARNING: Currently on branch '$CURRENT_BRANCH', expected '$BRANCH'"
  echo "Switch to '$BRANCH' first, or press Ctrl+C to abort."
  read -r -p "Continue anyway? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

echo "→ Building image..."
docker build -f Dockerfile.prod -t "${IMAGE}:latest" .

echo "→ Stopping old container..."
docker stop "$CONTAINER" 2>/dev/null || true
docker rm "$CONTAINER" 2>/dev/null || true

echo "→ Starting new container..."
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --env-file .env.production \
  --network tripsignal_tripsignal-network \
  -p "${PORT}:3000" \
  "${IMAGE}:latest"

echo "=== Done! Container '$CONTAINER' is running on port $PORT ==="
docker ps --filter "name=$CONTAINER" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
