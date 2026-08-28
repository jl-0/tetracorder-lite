#!/usr/bin/env bash
# Shared settings for the demo scripts. Sourced, never executed.
#
# Everything the demo generates lives under $WORK, outside the repository, so a
# run can never dirty the working tree or land in a container build context.
# $WORK is under $HOME, which Codespaces preserves across stop/start and
# captures in a prebuild -- the same lifecycle as the pulled image.

WORK="${TETRACORDER_WORK:-$HOME/tetracorder-demo}"
DATA="$WORK/data"
OUTPUT="$WORK/output"
SITE="$WORK/site"
STATE="$WORK/state"

IMAGE="${TETRACORDER_IMAGE:-ghcr.io/jl-0/tetracorder-lite:demo}"
SCENE_URL="${TETRACORDER_SCENE_URL:-https://github.com/jl-0/tetracorder-lite/releases/download/demo-data-v1/emit20250327t212148_100x100.tar.gz}"
PORT="${TETRACORDER_PORT:-8080}"

# Written by run-pipeline.sh and polled by the results page.
status() {
  mkdir -p "$SITE"
  printf '{"state":"%s","message":"%s","started":"%s","elapsed":%s}\n' \
    "$1" "$2" "${STARTED:-}" "${3:-0}" > "$SITE/status.json"
}
