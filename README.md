# tetracorder-lite

Containerized USGS Tetracorder (v6) for EMIT mineral identification, with a Python
CLI for running tetracorder and for rebuilding the convolved spectral library when a
new calibration epoch arrives.

## Quick start

```sh
# Run tetracorder on a mounted reflectance file (default action)
docker run --rm \
  -v /path/to/input:/data \
  -v /path/to/output:/output \
  tetracorder-lite

# Build a convolved spectral library for a new calibration epoch
docker run --rm \
  -v /path/to/data:/data \
  -v /path/to/output:/output \
  tetracorder-lite convolve \
    -m /data/splib06b \
    -t /data/s06emitc \
    -e /data/scene_rfl.hdr \
    -o /output/s06emit_new
```

## Building the container

```sh
podman build -f Containerfile -t tetracorder-lite .
# or
docker build --platform linux/amd64 -f Containerfile -t tetracorder-lite .
```

The image compiles specpr + Tetracorder (Fortran/ratfor), installs DaVinci, and
sets up the `tetrapy` Python environment via pixi.

## Commands

The container entrypoint is `tetrapy`. Pass a subcommand as the container argument
(defaults to `run` if omitted):

| Command | Description |
|---|---|
| `run` | **(default)** Setup then run tetracorder on the mounted input |
| `setup` | Configure a tetracorder run only (`cmd-setup-tetrun`) |
| `tetrun` | Execute a previously-configured run (`cmd.runtet`) |
| `convolve` | Rebuild the convolved spectral library (pure Python) |
| `validate` | Compare two convolved libraries (per-spectrum RMS) |

Use `--help` on any command for full options:

```sh
docker run --rm tetracorder-lite --help
docker run --rm tetracorder-lite convolve --help
```

## Volume contract

| Mount point | Purpose |
|---|---|
| `/data` | Input: reflectance file, master library (`splib06b`), template library, ENVI headers |
| `/output` | Output: tetracorder results or the newly-convolved library |

The unconvolved master library (`splib06b`) and template convolved library are
**mounted at runtime**, never baked into the image.

## Convolved spectral library

Tetracorder matches observed reflectance against a spectral library convolved to the
instrument's channels. When EMIT gets a new calibration (new wavelengths/FWHM), the
library must be regenerated. `tetrapy convolve` does this in **pure Python**:

1. Reads the unconvolved master library `splib06b` (specpr binary format)
2. Gaussian-convolves each spectrum from its native grid to the target EMIT grid
   (with native-FWHM quadrature correction)
3. Writes a valid specpr library using an existing convolved library as a structural
   template (preserves record indexing)

The target grid is taken from an EMIT reflectance ENVI header, so wavelength/FWHM
self-consistently match the scene.

```sh
# Convolve to a new epoch's grid
docker run --rm -v ./data:/data -v ./out:/output tetracorder-lite convolve \
  -m /data/splib06b -t /data/s06emitc -e /data/new_scene_rfl.hdr -o /output/s06emit_new

# Validate the result against a reference
docker run --rm -v ./out:/output -v ./ref:/ref tetracorder-lite validate \
  /output/s06emit_new /ref/s06emitc
```

Validated against the shipped `s06emitc`: 1365 spectra, median RMS 4.4e-5
(reflectance 0-1), identical file structure. See
[`docs/convolved-library-build.md`](docs/convolved-library-build.md) for the full
technical reference.

## Development

The Python CLI lives in `tetrapy/`. Managed with [pixi](https://pixi.sh):

```sh
pixi install
pixi run tetrapy --help
```

To run outside the container (needs numpy + click):

```sh
pip install numpy click
python -m tetrapy --help
```

## Project structure

```
tetracorder-lite/
  Containerfile          # container build (specpr + tetracorder + pixi/tetrapy)
  pyproject.toml         # Python project config (pixi workspace)
  tetrapy/              # Python CLI
    __main__.py         #   click CLI entrypoint
    tetra.py            #   tetracorder run/setup
    convolve.py         #   convolved-library builder (pure Python)
  tetracorder/          # vendored tetracorder + specpr source tree
  data/                 # mineral grouping matrix
  docs/                 # technical documentation
```
