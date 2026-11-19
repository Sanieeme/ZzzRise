#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Create .venv and install requirements first." >&2
  exit 1
fi
cd "$PROJECT_DIR"
exec "$PYTHON" -m pipeline.run_pipeline
