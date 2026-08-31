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

# Docker calls, bounded.
#
# The walkthrough runs as a folder-open task, which can start before
# docker-in-docker has finished coming up. A docker CLI call against a socket
# that exists but is not answering yet blocks, and several of them in a row
# make the editor look like it is hanging rather than waiting. `timeout` is
# util-linux, so it is present on Codespaces but not on macOS; without it the
# call simply runs unbounded, as it did before.
DOCKER_TIMEOUT="${TETRACORDER_DOCKER_TIMEOUT:-5}"
dk() {
  if command -v timeout >/dev/null 2>&1; then
    timeout "$DOCKER_TIMEOUT" docker "$@"
  else
    docker "$@"
  fi
}

# True if the docker daemon is answering right now. Used to tell "not done yet"
# apart from "cannot tell", so the walkthrough never reports a step as pending
# when it simply could not look.
docker_ready() { dk info >/dev/null 2>&1; }

# True if the named container exists and is running.
container_running() {
  [ "$(dk inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]
}

# Reads one value out of an ENVI header.
hdr_val() {
  sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\(.*\)$/\1/p" "$1" | head -1 | tr -d '[:space:]\r'
}

# Bytes an ENVI cube should occupy, from its header.
envi_expected_bytes() {
  local hdr=$1 s l b dt bpe
  s=$(hdr_val "$hdr" samples); l=$(hdr_val "$hdr" lines); b=$(hdr_val "$hdr" bands)
  dt=$(hdr_val "$hdr" "data type")
  case "$dt" in
    1) bpe=1 ;; 2|12) bpe=2 ;; 3|4|13) bpe=4 ;; 5) bpe=8 ;;
    *) return 1 ;;
  esac
  [ -n "$s" ] && [ -n "$l" ] && [ -n "$b" ] || return 1
  echo $(( s * l * b * bpe ))
}

# Checks the scene is complete, not merely present.
#
# A truncated download is the failure this exists for: the archive holds rfl,
# rfl.hdr, uncert, uncert.hdr in that order, so a stream that dies near the end
# leaves a perfectly good reflectance cube and a short uncertainty cube. The
# pipeline then runs for nine minutes before aggregate opens the uncertainty
# and rasterio says "Image file is too small". Checking only that scene_rfl
# exists -- which it does -- is what let that through.
verify_scene() {
  local role data hdr expected actual
  for role in rfl uncert; do
    data="$DATA/scene_$role"; hdr="$DATA/scene_$role.hdr"
    if [ ! -s "$data" ] || [ ! -s "$hdr" ]; then
      echo "[verify] missing $data or its header" >&2
      return 1
    fi
    if ! expected=$(envi_expected_bytes "$hdr"); then
      echo "[verify] could not read dimensions from $hdr" >&2
      return 1
    fi
    actual=$(wc -c < "$data" | tr -d ' ')
    if [ "$actual" -lt "$expected" ]; then
      echo "[verify] $data is truncated: $actual bytes, header implies $expected" >&2
      return 1
    fi
  done
  return 0
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
