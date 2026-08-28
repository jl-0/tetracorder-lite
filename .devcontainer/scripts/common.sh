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

# DaVinci ships amd64 only, so the image is amd64 only. Codespaces is amd64 and
# would not need this, but without it docker refuses to pull or run the image on
# an arm64 host ("no matching manifest for linux/arm64/v8") -- which is every
# Apple Silicon Mac these scripts get tested on.
PLATFORM="--platform=linux/amd64"

# Written by run-pipeline.sh and polled by the results page.
status() {
  mkdir -p "$SITE"
  printf '{"state":"%s","message":"%s","started":"%s","elapsed":%s}\n' \
    "$1" "$2" "${STARTED:-}" "${3:-0}" > "$SITE/status.json"
}
