#!/usr/bin/env bash
# Guided walkthrough of the Tetracorder demo.
#
# Safe to run at any time, as many times as you like. It works out where you
# are by looking at what actually exists -- the image, the scene, the results,
# the containers -- rather than by keeping a progress file that could disagree
# with reality. That is what makes it resume correctly after a codespace has
# been stopped and started, which terminates every running process.
set -uo pipefail
# The repository root, one level up from .devcontainer/.
cd "$(dirname "$0")/.."
source .devcontainer/scripts/common.sh

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\e[1m'; DIM=$'\e[2m'; OK=$'\e[32m'; WARN=$'\e[33m'; OFF=$'\e[0m'
else
  B=""; DIM=""; OK=""; WARN=""; OFF=""
fi

# Steps skipped in this session. Skipping cannot change what is actually on
# disk, so without remembering it here the walkthrough would offer the same
# step again immediately and never move on.
SKIPPED=""

skipped() { case " $SKIPPED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

TITLES=("Container image" "Sample scene" "Run Tetracorder" "Open the results")
NOTES=("ghcr.io/jl-0/tetracorder-lite:demo, 1.7 GB compressed" \
       "300x200 window of EMIT granule emit20250327t212148" \
       "convolve, setup, tetrun, aggregate -- about 9 minutes" \
       "mineral maps and the live log, on a forwarded port")
CMDS=(".devcontainer/scripts/get-image.sh" \
      ".devcontainer/scripts/get-scene.sh" \
      ".devcontainer/scripts/run-pipeline.sh" \
      ".devcontainer/scripts/serve-results.sh")
BLURBS=("Fetch the container image. It carries specpr, Tetracorder and DaVinci already compiled, so nothing is built here." \
        "Download the sample scene and check it arrived complete -- a truncated download is caught here rather than nine minutes into a run." \
        "Run the pipeline. It starts as a container and this terminal follows its log; Ctrl-C stops watching, the run keeps going." \
        "Start the results page. It streams the run log and shows the mineral maps when the run finishes.")

# Each step reports done / todo / running / broken by inspecting the world.
step_state() {
  case $1 in
    1) dk image inspect "$IMAGE" >/dev/null 2>&1 && echo done || echo todo ;;
    2) verify_scene 2>/dev/null && echo done || echo todo ;;
    3)
      if container_running "$RUN_CONTAINER"; then echo running
      elif [ -f "$SITE/results.json" ]; then echo done
      elif dk inspect "$RUN_CONTAINER" >/dev/null 2>&1; then echo broken
      else echo todo
      fi ;;
    4) container_running "$WEB_CONTAINER" && echo done || echo todo ;;
  esac
}

# Prefers the terminal so a step's own stdin cannot swallow the answer, but
# falls back to stdin so this is still usable non-interactively.
ask() {
  local __var=$1 __default=${2:-q} __reply
  # 2>/dev/null must come BEFORE the redirect: redirections are applied left to
  # right, so with it after, the shell reports a failed /dev/tty open to the
  # still-unredirected stderr. macOS reports /dev/tty readable even when it
  # cannot be opened, so the test alone is not enough either.
  if read -r __reply 2>/dev/null < /dev/tty; then
    :
  elif ! read -r __reply; then
    __reply=$__default
  fi
  printf -v "$__var" '%s' "$__reply"
}

mark() {
  case $1 in
    done)      printf '%s[x]%s' "$OK" "$OFF" ;;
    running)   printf '%s[~]%s' "$WARN" "$OFF" ;;
    broken)    printf '%s[!]%s' "$WARN" "$OFF" ;;
    *)         printf '[ ]' ;;
  esac
}

banner() {
  local i state next=0
  printf '\n  %sTetracorder demo%s\n\n' "$B" "$OFF"
  for i in 1 2 3 4; do
    state=$(step_state $i)
    if [ "$next" = 0 ] && [ "$state" != done ] && ! skipped "$i"; then next=$i; fi
    printf '  %s %d. %-18s %s%s%s\n' \
      "$(mark "$state")" "$i" "${TITLES[$((i-1))]}" "$DIM" "${NOTES[$((i-1))]}" "$OFF"
  done
  printf '\n'
  return $next
}

run_step() {
  local i=$1
  printf '%s' "$B"; printf '%s\n' "${BLURBS[$((i-1))]}" | fold -s -w 72 | sed 's/^/  /'
  printf '%s\n' "$OFF"
  printf '  This runs:  %s%s%s\n\n' "$DIM" "${CMDS[$((i-1))]}" "$OFF"
  printf '  %sEnter%s to run, %ss%s to skip, %sq%s to quit > ' "$B" "$OFF" "$B" "$OFF" "$B" "$OFF"
  ask reply
  case "$reply" in
    q|Q) printf '\n  Come back any time with .devcontainer/get-started.sh\n\n'; exit 0 ;;
    s|S) SKIPPED="$SKIPPED $i"; printf '\n  Skipped.\n'; return 2 ;;
  esac
  printf '\n'
  if ! bash "${CMDS[$((i-1))]}"; then
    printf '\n  %sThat step failed.%s Re-run ./get-started.sh to try again.\n\n' "$WARN" "$OFF"
    exit 1
  fi
  return 0
}

# docker-in-docker can still be starting when the folder-open task fires. Say
# so, once, instead of reporting every step as pending because nothing could be
# inspected.
if ! docker_ready; then
  printf '\n  Waiting for the docker daemon to start' 
  for _ in $(seq 1 30); do
    docker_ready && break
    printf '.'; sleep 2
  done
  if docker_ready; then printf ' ready\n'
  else
    printf '\n\n  The docker daemon is not responding. Try: sudo service docker start\n\n'
    exit 1
  fi
fi

while true; do
  banner; next=$?

  if [ "$next" = 0 ]; then
    if [ -n "$SKIPPED" ]; then
      printf '  %sNothing left to offer -- you skipped step(s):%s%s\n' "$WARN" "$OFF" "$SKIPPED"
      printf '  Run .devcontainer/get-started.sh again to be offered them afresh.\n\n'
      exit 0
    fi
    printf '  %sEverything is done.%s\n\n' "$OK" "$OFF"
    if [ -n "${CODESPACE_NAME:-}" ]; then
      printf '  Open the PORTS panel and click the globe icon on port %s.\n\n' "$PORT"
    else
      printf '  Open http://localhost:%s\n\n' "$PORT"
    fi
    printf '  %sRerun the pipeline:%s  .devcontainer/scripts/run-pipeline.sh\n' "$DIM" "$OFF"
    printf '  %sStart over:%s          .devcontainer/scripts/reset.sh\n\n' "$DIM" "$OFF"
    exit 0
  fi

  state=$(step_state "$next")

  if [ "$state" = running ]; then
    printf '  Step %d is running now.\n\n' "$next"
    printf '  %sEnter%s to watch it, %ss%s to leave it running > ' "$B" "$OFF" "$B" "$OFF"
    ask reply s
    case "$reply" in
      s|S) printf '\n  Still running. Check back with ./get-started.sh\n\n'; exit 0 ;;
      *)   printf '\n'; bash .devcontainer/scripts/watch.sh; continue ;;
    esac
  fi

  if [ "$state" = broken ]; then
    # A codespace stop kills every running process, so an interrupted run is
    # the normal consequence of pausing mid-pipeline rather than a real fault.
    code=$(docker inspect -f '{{.State.ExitCode}}' "$RUN_CONTAINER" 2>/dev/null)
    printf '  %sThe previous run did not finish%s (exit code %s).\n' "$WARN" "$OFF" "${code:-?}"
    printf '  Stopping a codespace terminates running processes, so this is\n'
    printf '  what a run that was interrupted by a pause looks like.\n\n'
    printf '  %sEnter%s to start it again, %sl%s to see its last output, %sq%s to quit > ' \
      "$B" "$OFF" "$B" "$OFF" "$B" "$OFF"
    ask reply
    case "$reply" in
      q|Q) printf '\n'; exit 0 ;;
      l|L) printf '\n'; docker logs --tail 40 "$RUN_CONTAINER" 2>&1; printf '\n'; continue ;;
    esac
    printf '\n'
    docker rm -f "$RUN_CONTAINER" >/dev/null 2>&1 || true
  fi

  run_step "$next" || true
  printf '\n'
done
