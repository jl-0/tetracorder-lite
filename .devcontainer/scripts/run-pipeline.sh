#!/usr/bin/env bash
# Runs the Tetracorder pipeline over the demo scene, then renders the imagery
# the results page shows. Started in the background by start.sh, so its progress
# is reported through $SITE/status.json and $SITE/run.log rather than a terminal.
set -uo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BEGAN=$SECONDS
mkdir -p "$SITE" "$OUTPUT"
: > "$SITE/run.log"

# tetrapy renders its progress with rich. Without a tty it still emits colour
# and OSC-8 hyperlink escapes, which are noise in a log file served to a
# browser; NO_COLOR and TERM=dumb ask rich for plain text, and a fixed width
# keeps it from guessing 80 and wrapping mid-path.
env=(-e NO_COLOR=1 -e TERM=dumb -e COLUMNS=120)

mounts=(
  -v "$DATA:/data:ro"
  -v "$OUTPUT:/output"
  -v "$PWD/.devcontainer/config.demo.yml:/config.demo.yml:ro"
  -v "$PWD/.devcontainer/tools:/tools:ro"
  -v "$SITE:/site"
)

# `setup` requires its output directory to be absent, so clear any previous
# attempt. This has to happen inside the container: the pipeline runs as root
# and Tetracorder's output tree is root-owned, which the unprivileged user a
# devcontainer runs as cannot remove from the outside.
docker run --rm "${mounts[@]}" "$IMAGE" rm -rf /output/demo

status running "running Tetracorder over the scene" $((SECONDS - BEGAN))

# Runtime here is dominated by fixed overhead, not by the number of pixels: the
# run emits roughly 2,400 mineral products whatever the scene size. Expect
# something in the 10-20 minute range on a Codespaces machine.
if ! docker run --rm "${env[@]}" "${mounts[@]}" "$IMAGE" \
     tetrapy run /config.demo.yml >> "$SITE/run.log" 2>&1; then
  status failed "tetrapy run failed -- see the log below" $((SECONDS - BEGAN))
  exit 1
fi

status rendering "rendering results" $((SECONDS - BEGAN))

if ! docker run --rm "${env[@]}" "${mounts[@]}" "$IMAGE" \
     python /tools/quicklook.py \
       --rfl /data/scene_rfl \
       --agg /output/demo/aggregate/agg.nc \
       --out /site >> "$SITE/run.log" 2>&1; then
  status failed "rendering failed -- see the log below" $((SECONDS - BEGAN))
  exit 1
fi

# Hand the results back to whoever is driving, so they can be opened, deleted
# and rerun over without sudo. Failure here is cosmetic -- the page reads the
# imagery either way -- so it must not fail the run.
docker run --rm "${mounts[@]}" "$IMAGE" \
  chown -R "$(id -u):$(id -g)" /output /site >> "$SITE/run.log" 2>&1 || true

status done "complete" $((SECONDS - BEGAN))
echo "[run] finished in $((SECONDS - BEGAN))s" >> "$SITE/run.log"
