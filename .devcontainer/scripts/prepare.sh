#!/usr/bin/env bash
# Fetches everything the demo needs: the container image and the scene.
#
# Runs as onCreateCommand, which Codespaces also runs during a prebuild. Both
# the pulled image (docker-in-docker keeps it on this container's filesystem)
# and the downloaded scene are therefore baked into a prebuild, so a codespace
# created from one starts with no download left to do.
#
# Nothing secret is created here, for the same reason: a prebuild is shared by
# every codespace made from it.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

mkdir -p "$DATA" "$OUTPUT" "$SITE" "$STATE"
wait_for_docker

if [ "${TETRACORDER_BUILD:-0}" = "1" ]; then
  # The image compiles specpr and Tetracorder from Fortran/ratfor sources and
  # installs DaVinci; expect this to take a while on a Codespaces machine.
  echo "[prepare] building $IMAGE from Containerfile"
  docker build $PLATFORM -f Containerfile -t "$IMAGE" .
elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
  # Already here -- a prebuild baked it in, or this is a restart. Pulling again
  # would cost minutes to confirm the same digest.
  echo "[prepare] $IMAGE is already present"
else
  echo "[prepare] pulling $IMAGE"
  docker pull $PLATFORM "$IMAGE"
fi

if [ ! -e "$DATA/scene_rfl" ]; then
  echo "[prepare] downloading scene from $SCENE_URL"
  curl -fL --retry 3 --retry-delay 5 "$SCENE_URL" | tar xz -C "$DATA"

  # config.demo.yml refers to the scene as scene_rfl / scene_uncert. The archive
  # keeps the granule's real name for provenance, so link the two together
  # rather than renaming and losing it.
  for role in rfl uncert; do
    for suffix in "" ".hdr"; do
      # -name "*_rfl" does not match "*_rfl.hdr", so each of the four files is
      # matched exactly once. find exits 0 on no match, so check explicitly
      # rather than letting an empty archive fail somewhere later.
      src="$(find "$DATA" -maxdepth 1 -name "*_${role}${suffix}" ! -name "scene_*" | head -1)"
      if [ -z "$src" ]; then
        echo "[prepare] ERROR: archive contains no *_${role}${suffix}" >&2
        exit 1
      fi
      ln -sf "$(basename "$src")" "$DATA/scene_${role}${suffix}"
    done
  done
fi

ls -la "$DATA"
echo "[prepare] ready"
