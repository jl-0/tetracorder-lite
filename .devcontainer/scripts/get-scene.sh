#!/usr/bin/env bash
# Step 2: get the sample scene, and check it actually arrived intact.
#
# Re-downloads when what is on disk is incomplete. That case is not
# hypothetical: a download that dies near the end of the archive leaves a
# complete reflectance cube and a truncated uncertainty cube, and the pipeline
# then runs for nine minutes before rasterio rejects the uncertainty with
# "Image file is too small". Checking only that the reflectance file exists is
# what let that through.
set -euo pipefail
cd "$(dirname "$0")/../.."
source .devcontainer/scripts/common.sh

mkdir -p "$DATA" "$OUTPUT" "$SITE" "$STATE"

if verify_scene 2>/dev/null; then
  echo "[scene] already present and complete"
  exit 0
fi

for attempt in 1 2; do
  echo "[scene] downloading from $SCENE_URL"
  # Straight to a file rather than piping into tar: a stream that dies mid-pipe
  # leaves tar having written partial files, which is exactly the failure this
  # step exists to prevent.
  archive="$DATA/.scene.tar.gz"
  rm -f "$archive"
  if ! curl -fL --retry 3 --retry-delay 5 -o "$archive" "$SCENE_URL"; then
    echo "[scene] download failed (attempt $attempt)" >&2
    continue
  fi
  if ! tar tzf "$archive" >/dev/null 2>&1; then
    echo "[scene] archive is corrupt (attempt $attempt)" >&2
    continue
  fi

  tar xzf "$archive" -C "$DATA"
  rm -f "$archive"

  # config.demo.yml refers to the scene as scene_rfl / scene_uncert. The
  # archive keeps the granule's real name for provenance, so link the two
  # together rather than renaming and losing it.
  for role in rfl uncert; do
    for suffix in "" ".hdr"; do
      # -name "*_rfl" does not match "*_rfl.hdr", so each of the four files is
      # matched exactly once. find exits 0 on no match, so check explicitly.
      src="$(find "$DATA" -maxdepth 1 -name "*_${role}${suffix}" ! -name "scene_*" | head -1)"
      if [ -z "$src" ]; then
        echo "[scene] ERROR: archive contains no *_${role}${suffix}" >&2
        exit 1
      fi
      ln -sf "$(basename "$src")" "$DATA/scene_${role}${suffix}"
    done
  done

  if verify_scene; then
    echo "[scene] ready"
    exit 0
  fi
  echo "[scene] downloaded copy is incomplete, retrying" >&2
done

echo "[scene] ERROR: could not obtain a complete scene" >&2
exit 1
