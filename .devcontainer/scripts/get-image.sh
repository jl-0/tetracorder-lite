#!/usr/bin/env bash
# Step 1: get the container image.
#
# Pulls the published image, or builds it from Containerfile when
# TETRACORDER_BUILD=1. Safe to re-run: an image that is already here is left
# alone rather than re-pulled to confirm the same digest.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh
wait_for_docker

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[image] $IMAGE is already here"
  exit 0
fi

if [ "${TETRACORDER_BUILD:-0}" = "1" ]; then
  # Compiles specpr and Tetracorder from Fortran/ratfor and installs DaVinci;
  # expect this to take a while on a Codespaces machine.
  echo "[image] building $IMAGE from Containerfile"
  docker build $PLATFORM -f Containerfile -t "$IMAGE" .
else
  echo "[image] pulling $IMAGE (about 1.7 GB compressed)"
  docker pull $PLATFORM "$IMAGE"
fi
echo "[image] ready"
