# Building the convolved spectral library inside the container

**Status:** trace / design note — first-stab scoping (Jeff), hands to James for container wiring.
**Context:** Tag-up 2026-06-25. Tetracorder 6.0 has new library entries not present in the
current convolved library, so we need to be able to **regenerate a convolved library from a
wavelength/FWHM pair** inside the container — today the container only ships a pre-convolved
EMIT library (`s06emitc`). Phil's reference recipe ("Library convolution example for current
system") is in `#emit-critical-minerals`, 2026-06-25 11:53.

Goal (Phil's framing): pre-convolve by wavelength to a fixed dir; the container takes the
library path as an input.

---

## What runs what (call graph)

Entry point (Phil's example uses the `s`/spectral side; there's a parallel `r`/reflectance side):

```
AAA.make.new.instrument.convolved.spectral.library.sh   (entry)
  ├─ make.new.convol.library.start.file
  │     ├─ make.new.restart.file
  │     ├─ dspecpr  < cmd.specpr.add.waves.resol.to.lib.noX      (specpr engine)
  │     ├─ cp startfiles/s06av95a.start  <lib>        ← HARDCODED template dep
  │     └─ spprint
  ├─ spsetwave / spprint                               (specpr support progs)
  └─ mak.convol.library
        ├─ mak.convolve.1.cmds        → writes conv.<lib>.cmds
        ├─ dspecpr < conv.<lib>.cmds  → convolves against splib06b
        └─ sp_stamp <lib>             (adds header so davinci can read it)

rlib (reflectance) side, if needed:
AAA.make.new.instrument.convolved.rlib-spectral.library.sh
  ├─ reads library06.conv/startfiles/<s-lib>.start + restartfiles/r.<s-lib>
  ├─ spsettitle / spsetwave / spprint
  ├─ mak.convol.library …
  └─ sanity count vs sprlb06a / sprlb06b
```

Inputs the user supplies: a **wavelength** file and an **FWHM** file (single-column ascii, microns).
Phil's note: these can be generated from a radiance ENVI header with a ~5-line python script —
a good `tetrapy` add-on.

---

## Already satisfied by the current container ✅

The `Containerfile` already:
- installs `gfortran`, `ratfor`, `make`, `gcc`, `g++`, `libx11-dev`;
- sets every `SP_*` / `SPECPR` / `F77` / `RF` env var specpr needs;
- **compiles specpr + support progs** via
  `AAA.INSTALL.specpr+support-progs-linux-upgrade.1.7.sh install`, installing
  `spprint`, `spsetwave`, `spsettitle`, `sp_stamp`, `dspecpr`, `specpr` to `/usr/local/bin`.

`spcmdf`, `sppad`, `sptype`, `spur` are **specpr interactive commands** (written into the
`.cmds` files, executed by the engine) — not separate binaries. No extra build needed.

So there is **no new build/toolchain work** — the gap is only missing scripts + data.

---

## Port manifest — what to copy into the container tree

Source of truth: `spectroscopy-tetracorder/sl1/usgs/` (server: `/store/shared/spectroscopy-tetracorder/sl1/usgs/`).
Destination: `tetracorder/sl1/usgs/` in this repo.

### `library06.conv/` — build scripts (tiny, ~44 KB total)
| File | Role |
|---|---|
| `AAA.make.new.instrument.convolved.spectral.library.sh` | entry point |
| `mak.convol.library` | drives convolution |
| `make.new.convol.library.start.file` | builds specpr start/restart files |
| `make.new.restart.file` | leaf: restart file |
| `mak.convolve.1.cmds` | generates the specpr `.cmds` |
| `cmd.specpr.add.waves.resol.to.lib.noX` | specpr command file (noX variant) |

### `library06.conv/` — required data/templates
| File | Size | Role | How |
|---|---|---|---|
| `startfiles/s06av95a.start` | 48 KB | template copied by `make.new.convol.library.start.file` (hardcoded name) | **committed** |
| `splib06b` | **20 MB** | unconvolved master source library — convolution reads this | **mounted** |

### `rlib06/` — reflectance-side library (part of Phil's full recipe)
| File | Size | Role | How |
|---|---|---|---|
| `AAA.make.new.instrument.convolved.rlib-spectral.library.sh` | — | rlib entry | **committed** |
| `sprlb06a`, `sprlb06b` | 2.8 / 2.9 MB | reference libs for the post-build sanity count (informational) | **mounted** |

**Not needed** (Phil: don't bring the giant convolved libraries): the per-instrument `conv.s06*.cmds`
+ `s06*` outputs, `splib06a` (50 MB), the `waves.ascii.files/` examples, READMEs.

---

## Decision: mount the master library at runtime (2026-06)

`splib06b` (20 MB binary specpr data) and the rlib reference libs are **not committed and not
baked into the image**. They are mounted at runtime and the container takes the library path as an
input — matching Phil's "container takes the library path as an input." Keeps repo/image lean.
Enforced by `.gitignore` + `.containerignore` entries so they can't be accidentally committed/baked.

### Proposed volume contract (for James's container wiring)
- Host mounts the master-library dir (server: `/store/shared/tetracorder_libraries/` and the
  source `splib06b` under `sl1/usgs/library06.conv/`) to a fixed in-container path, e.g. `/data`.
- Container accepts `--library-path <dir>` (tetrapy flag / env), pointing at the mounted master libs.
- specpr convolution scripts read `splib06b` from their CWD (`library06.conv/`), so the wiring must
  symlink/stage the mounted `splib06b` (and `sprlb06a/b` for the rlib count) into the workdir before
  running — or run the convolution in the mounted dir directly.
- **Convolved output**: publish to a fixed, mountable output dir the caller can retrieve (per Phil's
  "pre-convolve by wavelength to a fixed dir"). Decide the exact path with James.

## Verification items
- Confirm `/usr/local/bin` (specpr install target) is on `PATH` at runtime for the convolution scripts.
- Confirm `dspecpr` runs headless (`-g99` noX path) in the container — no X server.
- Run one end-to-end convolution from an EMIT wl/fwhm pair; diff against the shipped `s06emitc`.

## Hand-off
Jeff: this trace + first-cut file port. James: wire it into the `Containerfile` /
`tetrapy` flow as the optional library-build step. Still needed from Phil: confirm the
`emit_wl_*/emit_fwhm_*` input source (or the 5-line ENVI-header generator).
