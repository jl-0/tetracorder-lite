# tetracorder-lite

A containerized, single-purpose build of USGS **Tetracorder** (v6) with a small
Python CLI (`tetrapy`) for setting up and running mineral identification, and for
regenerating the convolved spectral library a run depends on.

## Build the container

```sh
podman build -f Containerfile -t tetracorder-lite .
# or: docker build --platform linux/amd64 -f Containerfile -t tetracorder-lite .
```

The image compiles specpr (Fortran/ratfor) and Tetracorder, installs ASU davinci,
and builds the `tetrapy` environment with pixi.

## Run tetracorder

Mount an input reflectance directory to `/data` and an output directory to `/output`:

```sh
podman run --rm \
  -v /path/to/input:/data \
  -v /path/to/output:/output \
  tetracorder-lite tetrapy run
```

`tetrapy` commands:

| Command | Purpose |
|---|---|
| `setup` | Configure a tetracorder run (`cmd-setup-tetrun`) |
| `tetrun` | Execute a configured run (`cmd.runtet`) |
| `run` | `setup` then `tetrun` |
| `convolve` | Regenerate the convolved spectral library in Python (see below) |
| `validate` | Compare two convolved libraries (per-spectrum RMS) |

Run `tetrapy <command> --help` for options.

## Regenerating the convolved spectral library

Tetracorder matches against a spectral library that has been **convolved** to the
instrument's channels. When a new calibration epoch appears (new wavelengths/FWHM),
the convolved library must be rebuilt. `tetrapy convolve` does this in **pure Python**
(a reimplementation of the USGS specpr convolution — no Fortran/specpr needed):

- reads the unconvolved master library `splib06b` (specpr format),
- Gaussian-convolves each spectrum from its native grid to the target EMIT grid
  (native-FWHM quadrature correction),
- writes a valid specpr library by overwriting the data in an existing convolved
  library used as a **template** (preserves record numbering / structure).

The target grid is taken from an **EMIT reflectance ENVI header** (`--envi-header`) —
reflectance is tetracorder's own input, so wavelength/FWHM self-consistently match the
scene, and only the small `.hdr` is needed. Omit it to reuse the template's grid.

```sh
podman run --rm \
  -v /path/to/data:/data \        # splib06b, template lib, and the scene .hdr
  -v /path/to/output:/output \
  tetracorder-lite \
  tetrapy convolve \
    -m /data/splib06b \                 # unconvolved master
    -t /data/s06emit_prior \            # prior convolved lib (structure/indexing template)
    -e /data/<scene>_rfl.hdr \          # target grid from EMIT reflectance header
    -o /output/s06emit_new
```

Validate against a reference library:

```sh
tetrapy validate /output/s06emit_new /data/s06emit_reference
# per-spectrum RMS stats
```

`splib06b` and the template are **mounted at runtime**, never baked into the image. See
[`docs/convolved-library-build.md`](docs/convolved-library-build.md) for the format
reference and validation (median RMS ~4.4e-5 vs the shipped `s06emitc`).
