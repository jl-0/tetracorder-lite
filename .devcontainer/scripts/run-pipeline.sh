#!/usr/bin/env bash
# Runs the Tetracorder pipeline over the demo scene, then renders the imagery
# the results page shows.
#
# Progress is reported two ways, because this normally runs detached from any
# terminal: everything the container prints is appended to $SITE/run.log, which
# the results page streams, and $SITE/status.json carries a heartbeat plus the
# current stage so a run that has died cannot look like a run that is working.
#
# Run it directly and it also streams to your terminal:
#   .devcontainer/scripts/run-pipeline.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

mkdir -p "$SITE" "$OUTPUT" "$STATE"
: > "$SITE/run.log"

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BEGAN=$SECONDS
STAGE=""

# Anything that kills the script -- including a failure in a `set -e`-less
# script that we did not anticipate -- must leave a status the page can show.
# Without this a crash leaves "running" on screen forever.
cleanup() {
  local rc=$?
  [ -n "${TAIL_PID:-}" ] && kill "$TAIL_PID" 2>/dev/null
  if [ "$rc" -ne 0 ] && ! grep -q '"state":"done"' "$SITE/status.json" 2>/dev/null; then
    status failed "the run exited unexpectedly (code $rc) -- see the log" "$((SECONDS - BEGAN))"
  fi
  rm -f "$STATE/run.pid"
}
trap cleanup EXIT

echo $$ > "$STATE/run.pid"

# When there is a terminal, mirror the log to it. When there is not (the usual
# case -- start.sh detaches this), skip it and let the page do the reporting.
if [ -t 1 ]; then
  tail -f "$SITE/run.log" &
  TAIL_PID=$!
fi

log() { printf '[run] %s\n' "$1" >> "$SITE/run.log"; snapshot; }

# The page polls the log every couple of seconds, and a full Tetracorder run
# log runs to megabytes -- python's http.server does not honour Range requests,
# so the page would re-download the whole thing each time. Keep a bounded tail
# for it to poll; run.log itself stays complete and is linked from the page.
# tetrun is the long stage and it is silent on stdout: tetrapy renders
# tetracorder's output through rich.Live, which draws almost nothing when
# stdout is not a terminal. The same output is written line-by-line, flushed,
# to {output}/tetracorder.out -- so that, not the pipeline's stdout, is what
# the page should stream while tetracorder is working.
TETOUT="$OUTPUT/demo/tetracorder/tetracorder.out"

snapshot() {
  local tmp="$SITE/run.tail.$$"
  {
    tail -n 120 "$SITE/run.log" 2>/dev/null
    if [ -s "$TETOUT" ]; then
      printf '\n---- tetracorder output (%s lines so far) ----\n' \
        "$(wc -l < "$TETOUT" | tr -d ' ')"
      tail -n 280 "$TETOUT" 2>/dev/null
    fi
  } > "$tmp" 2>/dev/null && mv -f "$tmp" "$SITE/run.tail"
}

# tetrapy drives its stages through a rich progress display; the stage name is
# the one stable thing in that output. Parsed leniently -- if the wording ever
# changes the heartbeat still ticks, the stepper just stops advancing.
current_stage() {
  local seen
  seen="$(tr -d '\000' < "$SITE/run.log" 2>/dev/null \
          | grep -oE 'Executing: [a-z_]+' | tail -1 | awk '{print $2}')"

  # rich.Live takes over the console for the whole of tetrun, so the last
  # "Executing:" line stdout ever shows is setup's -- the stepper would sit on
  # setup for the eight minutes that tetrun actually takes. tetracorder.out
  # only exists once tetrun is under way, so its presence is the reliable
  # signal. aggregate logs through tetrapy's logger again and so reappears on
  # stdout normally; do not override that.
  if [ -s "$TETOUT" ] && { [ "$seen" = "setup" ] || [ -z "$seen" ]; }; then
    echo tetrun
  else
    echo "$seen"
  fi
}

# Waits for a background container, refreshing status.json as it goes, and
# returns the container's exit code.
supervise() {
  local pid=$1 phase=$2 rc
  while kill -0 "$pid" 2>/dev/null; do
    local seen detail
    seen="$(current_stage)"
    [ -n "$seen" ] && STAGE="$seen"

    # A concrete count that moves, derived from the filesystem rather than from
    # a stream that may be buffered. During tetrun this is the only thing that
    # visibly advances for minutes at a time.
    detail=""
    if [ -d "$OUTPUT/demo/tetracorder" ]; then
      local n
      n=$(find "$OUTPUT/demo/tetracorder" -name '*.depth.gz' 2>/dev/null | wc -l | tr -d ' ')
      [ "${n:-0}" -gt 0 ] && detail=" — $n mineral products"
    fi

    if [ -n "$STAGE" ]; then
      status running "$phase: $STAGE$detail" "$((SECONDS - BEGAN))"
    else
      status running "$phase$detail" "$((SECONDS - BEGAN))"
    fi
    snapshot
    sleep 3
  done
  wait "$pid"; rc=$?
  return $rc
}

# `setup` requires its output directory to be absent, so clear any previous
# attempt. This has to happen inside the container: the pipeline runs as root
# and Tetracorder's output tree is root-owned, which the unprivileged user a
# devcontainer runs as cannot remove from the outside.
# Retried, and with a fallback, because `rm -rf` over a bind mount
# intermittently reports "Directory not empty" on a tree this size -- it is a
# filesystem artifact rather than a real permissions or state problem, and it
# has no business failing an otherwise good run. Moving the directory aside is
# enough: `setup` only requires that the path not exist.
clear_output() {
  local attempt
  for attempt in 1 2 3; do
    docker run --rm $PLATFORM "${mounts[@]}" "$IMAGE" \
      sh -c 'rm -rf /output/demo; [ ! -e /output/demo ]' >> "$SITE/run.log" 2>&1 && return 0
    log "output directory still present after attempt $attempt, retrying"
    sleep 2
  done
  log "falling back to moving the old output aside"
  docker run --rm $PLATFORM "${mounts[@]}" "$IMAGE" \
    sh -c 'mv /output/demo "/output/.stale-$(date +%s)" 2>/dev/null || true
           rm -rf /output/.stale-* 2>/dev/null || true
           [ ! -e /output/demo ]' >> "$SITE/run.log" 2>&1
}

status queued "preparing the output directory" 0
log "clearing $OUTPUT/demo"
if ! clear_output; then
  status failed "could not clear the output directory -- see the log" "$((SECONDS - BEGAN))"
  exit 1
fi

log "starting tetrapy run (this takes about nine minutes)"
docker run --rm $PLATFORM "${env[@]}" "${mounts[@]}" "$IMAGE" \
  tetrapy run /config.demo.yml >> "$SITE/run.log" 2>&1 &
if ! supervise $! "running Tetracorder"; then
  snapshot
  status failed "tetrapy run failed -- see the log" "$((SECONDS - BEGAN))"
  exit 1
fi

status rendering "rendering the mineral maps" "$((SECONDS - BEGAN))"
log "rendering imagery"
docker run --rm $PLATFORM "${env[@]}" "${mounts[@]}" "$IMAGE" \
  python /tools/quicklook.py \
    --rfl /data/scene_rfl \
    --agg /output/demo/aggregate/agg.nc \
    --out /site >> "$SITE/run.log" 2>&1 &
if ! supervise $! "rendering"; then
  snapshot
  status failed "rendering failed -- see the log" "$((SECONDS - BEGAN))"
  exit 1
fi

# Hand the results back to whoever is driving, so they can be opened, deleted
# and rerun over without sudo. Failure here is cosmetic -- the page reads the
# imagery either way -- so it must not fail the run.
docker run --rm $PLATFORM "${mounts[@]}" "$IMAGE" \
  chown -R "$(id -u):$(id -g)" /output /site >> "$SITE/run.log" 2>&1 || true

ELAPSED=$((SECONDS - BEGAN))
log "finished in ${ELAPSED}s"
snapshot
status done "complete" "$ELAPSED"
