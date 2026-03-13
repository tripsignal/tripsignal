#!/bin/bash
# Rebuild documentation site
# Usage: ./rebuild.sh [path-to-markdown]
set -e

DOCS_SOURCE="${1:-/home/trent/tripsignal-ui/docs/TRIPSIGNAL-DOCUMENTATION.md}"
DOCS_TARGET="/opt/tripsignal/docs/docs/index.md"

if [ -f "$DOCS_SOURCE" ]; then
  cp "$DOCS_SOURCE" "$DOCS_TARGET"
  echo "Copied latest documentation from $DOCS_SOURCE"
else
  echo "Warning: Source file not found at $DOCS_SOURCE"
  echo "Building with existing docs."
fi

cd /opt/tripsignal/docs
sudo docker run --rm -v /opt/tripsignal/docs:/docs squidfunk/mkdocs-material build
echo "Documentation site rebuilt successfully."
echo "Accessible via admin panel Docs tab."
