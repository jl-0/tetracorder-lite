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

# Named containers, because the docker daemon -- not a shell process -- is what
# owns the long-running work here. A Codespaces lifecycle command reaps anything
# it backgrounded when it exits, which killed both the web server and the run;
# a detached container survives that, and shows up in `docker ps` where you
# would look for it.
RUN_CONTAINER="${TETRACORDER_RUN_CONTAINER:-tetracorder-demo-run}"
WEB_CONTAINER="${TETRACORDER_WEB_CONTAINER:-tetracorder-demo-web}"

# True if the named container exists and is running.
container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]
}

# On a codespace *start* (as opposed to create) postStartCommand can run before
# docker-in-docker has finished coming up, and every docker call here would then
# fail for a reason that looks exactly like the bug this design already fixed:
# no containers, no obvious error. Wait rather than race.
wait_for_docker() {
  local i
  for i in $(seq 1 60); do
    docker info >/dev/null 2>&1 && return 0
    [ "$i" = 1 ] && echo "[common] waiting for the docker daemon"
    sleep 2
  done
  echo "[common] ERROR: the docker daemon did not become ready after 120s" >&2
  echo "[common] try: sudo service docker start" >&2
  return 1
}

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
