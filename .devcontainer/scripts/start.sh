#!/usr/bin/env bash
# Brings up the results page and kicks off the pipeline behind it.
#
# Runs as postStartCommand, so it must be safe to run again on every restart,
# and it must not block: a full run takes far longer than anyone wants to watch
# a lifecycle command sit there. The page comes up immediately and reports
# progress by polling status.json.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

mkdir -p "$SITE" "$STATE"
cp .devcontainer/site/index.html "$SITE/index.html"

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

if [ -f "$SITE/results.json" ]; then
  echo "[start] results from a previous run are already present"
  status done "complete" 0
elif pgrep -f run-pipeline.sh >/dev/null 2>&1; then
  echo "[start] a run is already in progress"
else
  STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)" status queued "starting" 0
  nohup .devcontainer/scripts/run-pipeline.sh > "$STATE/run-pipeline.log" 2>&1 &
  echo "[start] pipeline started in the background"
fi

cat <<MSG

  Tetracorder demo

    Results page:  $URL

  The page opens straight away and refreshes itself as the run progresses.
  A full run is around nine minutes: the cost is fixed overhead (~2,400
  mineral products per run), not the 100x100 scene.

  Rerun by hand with:
    .devcontainer/scripts/run-pipeline.sh

MSG
