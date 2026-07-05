# Building the convolved spectral library (Python)

**Status:** implemented + validated. Pure-Python reimplementation of the USGS specpr
convolution — no Fortran/specpr needed for library building.

## What this does

Tetracorder matches observed reflectance against a spectral library **convolved** to
the instrument's channels. New calibration epoch → new wavelengths/FWHM → the
convolved library must be regenerated. `tetrapy convolve` does this in Python:

1. **Read** the unconvolved master library `splib06b` (specpr binary format).
2. **Convolve** each spectrum from its own native grid + resolution onto the target
   EMIT grid: Gaussian-weighted resample with a native-FWHM quadrature correction
   (`σ_eff = √(FWHM_target² − FWHM_native²)`). Each spectrum's native wavelengths come
   from its `irwav` record; native FWHM from the paired "Bandpass (FWHM)" record.
3. **Write** a valid specpr library by overwriting the numeric data in an existing
   convolved library used as a **template** — preserving record numbering, titles,
   text/description records, and pointers exactly (Phil's "conserve indexing").

The target grid comes from an EMIT **reflectance ENVI header** (`--envi-header`), so
wavelength/FWHM self-consistently match the scene; omit it to reuse the template grid.

## Validation

Reconvolving `splib06b` and diffing against the shipped `s06emitc`:

```
tetrapy convolve -m splib06b -t s06emitc -o out         # reuse s06emitc's grid
tetrapy validate out s06emitc
# 1365 spectra: median RMS=4.4e-05 mean=7.6e-05 p95=2.5e-04 max=1.4e-03
```

Median per-spectrum RMS **~4.4e-5** (reflectance 0-1), identical file structure/size.
That reproduces specpr's convolution to ~0.004% — well below any matching threshold.

## Usage

```sh
# new calibration epoch: convolve splib06b to an EMIT scene's grid, reusing a prior
# EMIT convolved library for structure/indexing
tetrapy convolve \
  -m /data/splib06b \
  -t /data/s06emit_prior \
  -e /data/<scene>_rfl.hdr \
  -o /output/s06emit_new
```

`splib06b` and the template are **mounted at runtime** (not baked) — enforced by
`.gitignore` / `.containerignore`.

## Format reference

specpr = 1536-byte big-endian records, `icflag % 4` selects record type (0 data-head,
1 data-cont, 2 text-head, 3 text-cont). A spectrum's header carries `itchan`, `irwav`
(wavelength record #), `irespt` (resolution record #). Full spec:
`specpr/specpr-format-2,3/specpr-format-v2.txt`; struct offsets cross-checked against
the vendored opalpy reader and the C++ `Spectral-Library-Reader`. `tetrapy/convolve.py`
has the reader/writer.

## Why not drive specpr itself?

Earlier attempt drove the container's specpr build via its convolution scripts. Dead
end: the container's gfortran-built specpr throws direct-access file I/O errors
(`mathin: ERROR on device 15`, gfortran error 5002) during the convolution math —
a specpr build issue (suspected `recl`-unit / scratch-file handling). The convolution
math itself is trivial (a Gaussian resample), so reimplementing in Python sidesteps the
whole legacy-tool problem and matches where the team wants this to go. The specpr build
still matters for running Tetracorder itself — that's separate and unaffected.
