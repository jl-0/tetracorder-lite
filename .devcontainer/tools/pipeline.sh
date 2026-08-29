#!/usr/bin/env bash
# The whole pipeline, run INSIDE the container.
#
# This lives here rather than on the host because a Codespaces lifecycle
# command cannot leave background processes behind: anything backgrounded from
# postStartCommand is reaped when that command exits, taking the run and the
# web server with it. Handing the work to the docker daemon instead -- one
# detached container that owns the whole sequence -- is what makes it survive,
# and has the side benefit that `docker ps` shows the run actually happening.
#
# Writes progress to /site: run.log (complete), run.tail (bounded, what the
# page polls) and status.json (state, stage, heartbeat).
set -uo pipefail

SITE=/site
OUTPUT=/output
TETOUT="$OUTPUT/demo/tetracorder/tetracorder.out"
STAGES='["convolve","sensor","setup","tetrun","aggregate","render"]'

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BEGAN=$SECONDS
STAGE=""

mkdir -p "$SITE"
: > "$SITE/run.log"

status() {
  local tmp="$SITE/.status.$$"
  cat > "$tmp" <<JSON
{"state":"$1","message":"$2","stage":"${STAGE:-}","stages":$STAGES,
 "started":"$STARTED","elapsed":${3:-0},"heartbeat":$(date +%s)}
JSON
  # Rename rather than write in place: the page polls this constantly and would
  # otherwise sometimes fetch a half-written file and fail to parse it.
  mv -f "$tmp" "$SITE/status.json"
}

# The page polls the log every couple of seconds and a full Tetracorder run log
# runs to megabytes, which python's http.server would re-send in full each time
# because it does not honour Range requests. Keep a bounded tail for it.
#
# tetrun is the long stage and it is silent on stdout: tetrapy renders
# tetracorder's output through rich.Live, which draws almost nothing when
# stdout is not a terminal. The same output is written line-by-line and flushed
# to tetracorder.out, so that is what the page should actually stream.
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

# Appends only -- no tee. The streamer below already mirrors run.log to stdout,
# so teeing would print every milestone line to docker logs twice.
log() { printf '[run] %s\n' "$1" >> "$SITE/run.log"; snapshot; }

# Mirror both logs to the container's stdout, so `docker logs -f` is a real
# view of the run and not just a handful of milestone lines. tetrapy's own
# output is redirected to run.log rather than inherited, and the verbose
# tetracorder output only ever goes to tetracorder.out, so neither reaches
# stdout on its own. tetracorder.out does not exist until tetrun starts, hence
# the wait.
STREAM_PIDS=()
start_streaming() {
  tail -f "$SITE/run.log" 2>/dev/null & STREAM_PIDS+=($!)
  ( while [ ! -e "$TETOUT" ]; do sleep 2; done
    tail -f "$TETOUT" 2>/dev/null ) & STREAM_PIDS+=($!)
}
stop_streaming() {
  # Let the tails drain what they have before cutting them off, or the last
  # few lines of a finished run never reach docker logs.
  sleep 1
  for pid in "${STREAM_PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
  done
}
trap stop_streaming EXIT
start_streaming

current_stage() {
  local seen
  seen="$(tr -d '\000' < "$SITE/run.log" 2>/dev/null \
          | grep -oE 'Executing: [a-z_]+' | tail -1 | awk '{print $2}')"
  # rich.Live owns the console for the whole of tetrun, so the last "Executing:"
  # line stdout ever shows is setup's -- the stepper would sit on setup for the
  # eight minutes tetrun actually takes. tetracorder.out only exists once tetrun
  # is under way, so its presence is the reliable signal. aggregate logs through
  # tetrapy's logger again and reappears normally; do not override that.
  if [ -s "$TETOUT" ] && { [ "$seen" = "setup" ] || [ -z "$seen" ]; }; then
    echo tetrun
  else
    echo "$seen"
  fi
}

# Supervises a background child, refreshing status as it goes.
supervise() {
  local pid=$1 phase=$2 rc seen detail n
  while kill -0 "$pid" 2>/dev/null; do
    seen="$(current_stage)"
    [ -n "$seen" ] && STAGE="$seen"

    # A concrete count that moves, taken from the filesystem rather than from a
    # stream that may be buffered. During tetrun this is the only thing that
    # visibly advances for minutes at a time.
    detail=""
    if [ -d "$OUTPUT/demo/tetracorder" ]; then
      n=$(find "$OUTPUT/demo/tetracorder" -name '*.depth.gz' 2>/dev/null | wc -l | tr -d ' ')
      [ "${n:-0}" -gt 0 ] && detail=" — $n mineral products"
    fi

    status running "${phase}${STAGE:+: $STAGE}$detail" "$((SECONDS - BEGAN))"
    snapshot
    sleep 3
  done
  wait "$pid"; rc=$?
  snapshot
  return $rc
}

fail() { status failed "$1" "$((SECONDS - BEGAN))"; snapshot; exit 1; }

status queued "preparing the output directory" 0
log "clearing $OUTPUT/demo"
# Retried because rm -rf over a bind mount intermittently reports "Directory
# not empty" on a tree this size. Moving it aside is enough either way: setup
# only requires that the path not exist.
for attempt in 1 2 3; do
  rm -rf "$OUTPUT/demo" 2>>"$SITE/run.log"
  [ ! -e "$OUTPUT/demo" ] && break
  log "output directory still present after attempt $attempt, retrying"
  sleep 2
done
if [ -e "$OUTPUT/demo" ]; then
  log "moving the old output aside"
  mv "$OUTPUT/demo" "$OUTPUT/.stale-$(date +%s)" 2>>"$SITE/run.log" || true
  rm -rf "$OUTPUT"/.stale-* 2>/dev/null || true
fi
[ -e "$OUTPUT/demo" ] && fail "could not clear the output directory -- see the log"

log "starting tetrapy run (about nine minutes)"
tetrapy run /config.demo.yml >> "$SITE/run.log" 2>&1 &
supervise $! "running Tetracorder" || fail "tetrapy run failed -- see the log"

STAGE=render
status rendering "rendering the mineral maps" "$((SECONDS - BEGAN))"
log "rendering imagery"
python /tools/quicklook.py \
  --rfl /data/scene_rfl \
  --agg "$OUTPUT/demo/aggregate/agg.nc" \
  --out "$SITE" >> "$SITE/run.log" 2>&1 &
supervise $! "rendering" || fail "rendering failed -- see the log"

# Hand the results back to the host user so they can be opened, deleted and
# rerun over without sudo. Cosmetic, so it must not fail the run.
if [ -n "${HOST_UID:-}" ]; then
  chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$OUTPUT" "$SITE" 2>/dev/null || true
fi

ELAPSED=$((SECONDS - BEGAN))
log "finished in ${ELAPSED}s"
status done "complete" "$ELAPSED"
snapshot
