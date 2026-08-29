#!/usr/bin/env bash
# Brings up the results page and kicks off the pipeline behind it.
#
# Runs as postStartCommand, so it must be safe to run again on every restart,
# and it must not block: a full run takes far longer than anyone wants to watch
# a lifecycle command sit there.
#
#   start.sh            serve, and start a run in the background
#   start.sh --follow   serve, and run in the foreground streaming to this terminal
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

FOLLOW=0
case "${1:-}" in
  "")            ;;
  -f|--follow)   FOLLOW=1 ;;
  *) echo "usage: start.sh [--follow]" >&2; exit 2 ;;
esac

mkdir -p "$SITE" "$STATE"

# .devcontainer/page, not .devcontainer/site: the repository's .gitignore
# excludes site/ (built documentation), which silently kept this file out of
# the first commit and left postStartCommand failing on a cp of a file that
# was never pushed. Checked explicitly so a future recurrence says why.
TEMPLATE=.devcontainer/page/index.html
if [ ! -f "$TEMPLATE" ]; then
  echo "[start] ERROR: $TEMPLATE is missing from the checkout." >&2
  echo "[start] If it exists locally but not here, check .gitignore." >&2
  exit 1
fi
cp "$TEMPLATE" "$SITE/index.html"

if [ ! -e "$DATA/scene_rfl" ]; then
  echo "[start] scene is missing; running prepare.sh"
  .devcontainer/scripts/prepare.sh
fi

if ! pgrep -f "http.server $PORT" >/dev/null 2>&1; then
  nohup python3 -m http.server "$PORT" --directory "$SITE" \
    > "$STATE/http.log" 2>&1 &
  echo "[start] serving $SITE on port $PORT"
fi

URL="http://localhost:$PORT"
if [ -n "${CODESPACE_NAME:-}" ]; then
  URL="https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
fi

# A recorded pid, not `pgrep -f run-pipeline.sh`. pgrep matched anything with
# that string on its command line -- including a stalled or half-dead earlier
# attempt -- so start.sh would decline to start a run, print something that was
# not an error, and leave the page waiting on a pipeline that was never coming.
running() {
  local pid
  [ -f "$STATE/run.pid" ] || return 1
  pid="$(cat "$STATE/run.pid" 2>/dev/null)" || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

if [ "$FOLLOW" = 1 ]; then
  echo "[start] running in the foreground; results at $URL"
  exec .devcontainer/scripts/run-pipeline.sh
fi

if running; then
  echo "[start] a run is already in progress (pid $(cat "$STATE/run.pid"))"
elif [ -f "$SITE/results.json" ]; then
  echo "[start] results from a previous run are already present"
else
  rm -f "$STATE/run.pid"
  # setsid puts the pipeline in its own session so it survives this script's
  # process group being cleaned up when the lifecycle command finishes. It is
  # util-linux, so it is there on Codespaces but not on macOS, where these
  # scripts also get tested -- fall back to plain nohup rather than failing.
  # </dev/null so it can never block on stdin.
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup .devcontainer/scripts/run-pipeline.sh \
      </dev/null > "$STATE/run-pipeline.log" 2>&1 &
  else
    nohup .devcontainer/scripts/run-pipeline.sh \
      </dev/null > "$STATE/run-pipeline.log" 2>&1 &
  fi
  disown 2>/dev/null || true

  # Do not just claim it started. If it died on its way to the first container
  # -- a missing image, a docker daemon that is not up -- say so here, with the
  # reason, rather than leaving the page spinning on a run that never existed.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    running && break
    sleep 1
  done
  if running; then
    echo "[start] pipeline started (pid $(cat "$STATE/run.pid"))"
  else
    echo "[start] ERROR: the pipeline exited immediately. Output:" >&2
    sed 's/^/[start]   /' "$STATE/run-pipeline.log" >&2
    tail -20 "$SITE/run.log" 2>/dev/null | sed 's/^/[start]   /' >&2
    exit 1
  fi
fi

cat <<MSG

  Tetracorder demo

    Results page:  $URL

  The page streams the run log live and shows the mineral maps underneath
  when the run finishes. A full run is around nine minutes: the cost is
  fixed overhead (~2,400 mineral products per run), not the 100x100 scene.

  Watch it here instead:   tail -f $SITE/run.log
  Run in this terminal:    .devcontainer/scripts/start.sh --follow
  Rerun from scratch:      .devcontainer/scripts/run-pipeline.sh

MSG
