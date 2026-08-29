#!/usr/bin/env bash
# Brings up the results page and starts the pipeline behind it.
#
# Runs as postStartCommand, so it must be safe to run again on every restart
# and it must not block. Critically, it must not leave background *processes*
# behind either: Codespaces reaps anything a lifecycle command backgrounded
# when that command exits, which is what killed both the web server and the
# run. Both are containers now, so the docker daemon owns them.
#
#   start.sh            start the page and the run, return immediately
#   start.sh --follow   start them, then stream the run to this terminal
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

FOLLOW=0
case "${1:-}" in
  "")            ;;
  -f|--follow)   FOLLOW=1 ;;
  *) echo "usage: start.sh [--follow]" >&2; exit 2 ;;
esac

mkdir -p "$SITE" "$OUTPUT" "$STATE"

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

# --restart unless-stopped so the page comes back by itself after a codespace
# stop/start or a docker daemon restart, without waiting for this script.
if container_running "$WEB_CONTAINER"; then
  echo "[start] $WEB_CONTAINER is already serving on port $PORT"
else
  docker rm -f "$WEB_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$WEB_CONTAINER" $PLATFORM \
    --restart unless-stopped \
    -p "$PORT:$PORT" \
    -v "$SITE:/site" \
    "$IMAGE" python -m http.server "$PORT" --directory /site >/dev/null
  echo "[start] serving $SITE on port $PORT ($WEB_CONTAINER)"
fi

if [ -f "$SITE/results.json" ] && ! container_running "$RUN_CONTAINER"; then
  echo "[start] results from a previous run are already present"
else
  .devcontainer/scripts/run-pipeline.sh < /dev/null
fi

# Deliberately NOT printing the forwarded URL. Codespaces treats CODESPACE_NAME
# and the port-forwarding domain as secrets and redacts them from lifecycle
# logs, so the line came out as "https://********-8080.********" -- which reads
# like the script failed to build it. Point at the Ports panel instead.
echo
echo "  Tetracorder demo"
echo
if [ -n "${CODESPACE_NAME:-}" ]; then
  echo "    Open the PORTS panel and click the globe icon on port $PORT."
  echo "    (VS Code opens it for you the first time. The URL is redacted"
  echo "     from this log by Codespaces, which is why it is not printed.)"
else
  echo "    Results page:  http://localhost:$PORT"
fi
cat <<MSG

  The page streams the run log live and shows the mineral maps underneath
  when the run finishes. A full run is around nine minutes: the cost is
  fixed overhead (~2,400 mineral products per run), not the 100x100 scene.

  Watch it here:        docker logs -f $RUN_CONTAINER
  Check it is running:  docker ps
  Rerun from scratch:   .devcontainer/scripts/run-pipeline.sh

MSG

if [ "$FOLLOW" = 1 ]; then
  docker logs -f "$RUN_CONTAINER"
fi
