#!/bin/sh
cd "$(dirname "$0")" || exit 1
POLITICA_PROJECT_ROOT=$(pwd)
export POLITICA_PROJECT_ROOT
if ! command -v uv >/dev/null 2>&1; then
  echo "Politica needs the uv Python package manager."
  echo "Install it once with: python3 -m pip install uv"
  exit 1
fi
uv sync --locked --python 3.12 --no-editable || exit 1
uv run --no-sync python -m politica_erd.app.cli
