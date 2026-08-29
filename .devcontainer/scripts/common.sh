#!/usr/bin/env bash
# Shared settings for the demo scripts. Sourced, never executed, and always
# from the repository root so the $PWD-relative mounts below resolve.
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

# PYTHONUNBUFFERED matters more than it looks: without it Python block-buffers
# stdout when it is a file rather than a terminal, so the log the page streams
# arrives in silent 8 KB bursts and the run looks hung. NO_COLOR and TERM=dumb
# ask rich for plain text instead of colour and OSC-8 hyperlink escapes, and a
# fixed width stops it guessing 80 and wrapping mid-path.
env=(-e PYTHONUNBUFFERED=1 -e NO_COLOR=1 -e TERM=dumb -e COLUMNS=120)

mounts=(
  -v "$PWD/.devcontainer/config.demo.yml:/config.demo.yml:ro"
  -v "$PWD/.devcontainer/tools:/tools:ro"
  -v "$DATA:/data:ro"
  -v "$OUTPUT:/output"
  -v "$SITE:/site"
)

# The ordered stages, so the page can draw a stepper rather than a spinner. The
# first five are tetrapy's own pipeline stages; render is ours.
STAGES='["convolve","sensor","setup","tetrun","aggregate","render"]'

# Written by run-pipeline.sh and polled by the results page.
#   status <state> <message> [elapsed]
# `heartbeat` is what lets the page tell a slow stage from a dead run: it is
# refreshed every few seconds while the pipeline is supervised, so a stale one
# means the run is gone, not just quiet.
status() {
  mkdir -p "$SITE"
  local tmp="$SITE/.status.$$"
  cat > "$tmp" <<JSON
{"state":"$1","message":"$2","stage":"${STAGE:-}","stages":$STAGES,
 "started":"${STARTED:-}","elapsed":${3:-0},"heartbeat":$(date +%s)}
JSON
  # Rename rather than write in place: the page polls this file constantly and
  # would otherwise sometimes fetch a half-written one and fail to parse it.
  mv -f "$tmp" "$SITE/status.json"
}
