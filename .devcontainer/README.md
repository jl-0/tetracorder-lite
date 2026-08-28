# Codespaces demo

Opens a codespace that runs Tetracorder end to end over a real EMIT L2A scene
and shows the result on a web page, with nothing to install and nothing to
configure.

```
[Open in GitHub Codespaces]  ->  pulls the image, downloads the scene,
                                 runs the pipeline, opens the results page
```

## One-time setup on the fork

Three things have to exist before the badge works for anyone who clicks it:

1. **Publish the scene.** Create a release tagged `demo-data-v1` and attach the
   archive built by `tools/make_subset.py` (see [The scene](#the-scene)). The
   URL in `devcontainer.json` points at
   `releases/download/demo-data-v1/emit20250327t212148_100x100.tar.gz`.

2. **Push the branch** so `.github/workflows/container.yml` runs and publishes
   `ghcr.io/jl-0/tetracorder-lite:demo`.

3. **Make the package public.** GHCR packages are private by default, and a
   private one cannot be pulled by a visitor's codespace. Package settings
   &rarr; Change visibility &rarr; Public. Skipping this is the failure that
   looks like a broken demo rather than a permissions problem.

Optionally enable a prebuild on this branch (Settings &rarr; Codespaces &rarr;
Set up prebuild). `onCreateCommand` does the fetching, so a prebuild removes
both the image pull and the scene download from a visitor's first minute.

## What it does

`onCreateCommand` (`scripts/prepare.sh`) fetches the two large things:

* the container image, pulled from `ghcr.io/jl-0/tetracorder-lite:demo`
* the scene, a 100&times;100 window of EMIT granule `emit20250327t212148`,
  downloaded from a release asset (17 MB)

`postStartCommand` (`scripts/start.sh`) serves the results page on port 8080 and
starts `scripts/run-pipeline.sh` in the background. The page comes up
immediately and polls `status.json`, so nobody watches a lifecycle command sit
there for ten minutes. The pipeline runs the four `tetrapy` stages from
`config.demo.yml` — convolve, setup, tetrun, aggregate — and then
`tools/quicklook.py` renders the imagery the page displays.

## Why it is built this way

**The image is pulled, not built.** The build compiles specpr and Tetracorder
from Fortran/ratfor and installs DaVinci. That is not something to do on every
codespace create. `.github/workflows/container.yml` builds and pushes it on
every push to this branch instead.

To exercise the build itself, pick **Tetracorder demo (build from source)** in
the "Dev container configuration" dropdown on the Codespaces options page. That
configuration sets `TETRACORDER_BUILD=1` and builds from `Containerfile`. It is
worth having: a clean build is the only place regressions like a dropped
executable bit on `tetracorder/` show up, because a published image carries the
artefacts of whatever tree built it.

**`onCreateCommand`, not `postCreateCommand`.** Codespaces runs `onCreate`
during a prebuild, and with docker-in-docker a pulled image lives on the
container's own filesystem. So a prebuild bakes in both the image and the
scene, and a codespace created from one has nothing left to download. Enable
prebuilds under Settings &rarr; Codespaces to get that.

**Nothing secret is generated anywhere.** A prebuild is shared by every
codespace made from it, so anything generated during `onCreate` would be shared
too. There is nothing here that needs a secret.

**Everything generated lives in `$HOME/tetracorder-demo`, outside the repo.** A
run cannot dirty the working tree or land in a Docker build context. `$HOME`
also has the right lifecycle: preserved across stop/start, discarded on rebuild.

## Runtime

Measured on this branch, 4 vCPU, amd64 under Rosetta on an Apple M5 Max
(Colima), 100x100 scene, timed end to end including rendering:

| | |
|---|---|
| with the overlay imagery Tetracorder writes by default | 10m 12s |
| with `nodualimages` / `noredoverlayimages`, as configured here | **8m 52s** |

Both produce identical mineralogy — 56.0% of pixels identified in group 1 over
20 materials, 99.2% in group 2 over 44 — so the overlays cost time and nothing
else. A Codespaces machine runs amd64 natively rather than emulated, so expect
the same or better; that figure has not been measured directly.

Tetracorder's cost is close to fixed — a run emits roughly 2,400 mineral
products regardless of scene size — so **the scene being small does not make it
fast**. Previously measured on the same hardware: a 100x100 subset 8m38s, a
200x200 subset 10m01s, a full 1242x1280 granule 33m38s. Cores, not pixels, set
the wall clock, which is why the root configuration asks for 4.

Much of the tail is single-threaded (gzipping several thousand small output
files), so extra cores past 4 buy less than the first two do.

## The scene

`tools/make_subset.py` cuts the window out of a full granule. The window at
line 1150, sample 200 was chosen by scanning the granule for the strongest mean
2.2 &micro;m absorption — the clay/mica feature Tetracorder's group 2 keys on —
so the demo shows real mineralogy rather than water or cloud.

The crop is spatial only. Every channel is kept, because the `convolve` stage
reads its target wavelength/FWHM grid from the reflectance header and the
convolved library has to match the scene channel for channel.

To cut a different one:

```sh
python .devcontainer/tools/make_subset.py \
  in/emit20250327t212148 /tmp/subset/myscene \
  --line 1150 --sample 200 --size 100

tar czf myscene.tar.gz -C /tmp/subset myscene_rfl myscene_rfl.hdr \
                                      myscene_uncert myscene_uncert.hdr
```

Attach the archive to a release and point `TETRACORDER_SCENE_URL` at it. The
archive must contain all four files; `prepare.sh` links them to the
`scene_rfl` / `scene_uncert` names `config.demo.yml` refers to.

Public EMIT L2A reflectance (`EMITL2ARFL`) is distributed by the LP DAAC as
NetCDF and needs a (free) Earthdata login. The ENVI pair `tetrapy` consumes is
an SDS intermediate and is not archived publicly, which is why the demo ships a
subset rather than downloading one.

## Running it by hand

```sh
.devcontainer/scripts/run-pipeline.sh          # rerun the pipeline
tail -f ~/tetracorder-demo/site/run.log        # watch it
```

Overrides, all read by `scripts/common.sh`:

| Variable | Default |
|---|---|
| `TETRACORDER_IMAGE` | `ghcr.io/jl-0/tetracorder-lite:demo` |
| `TETRACORDER_BUILD` | `0` — set to `1` to build from `Containerfile` |
| `TETRACORDER_SCENE_URL` | the `demo-data-v1` release asset |
| `TETRACORDER_WORK` | `$HOME/tetracorder-demo` |
| `TETRACORDER_PORT` | `8080` |
