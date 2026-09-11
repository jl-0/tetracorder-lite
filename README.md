# tetracorder-lite

Containerized USGS Tetracorder (v6) for EMIT mineral identification, with a Python
CLI (`tetrapy`) that drives the full pipeline — convolving the spectral library for a
new calibration epoch, setting up and running tetracorder, and aggregating the results
into L2B mineral products — from a single YAML config.

Note - this is not the authoritative version of Tetracorder. Please see [here](https://github.com/PSI-edu/spectroscopy-tetracorder)
if that's what you're after.  This version is what is used by EMIT - the core code is consistent,
and we will work to keep this in-sync with the original codebase.  Major difference are that this
version does not hold all convolved libraries to keep the containers small (they are instead
intended to be convolved by the the container).  Python scripts for output conversion
are also included, and will be expanded upon in the future.

This repository is also a work in progress,
that we are continuing to try and revise and simplify to aid in community use and uptake
of Tetracorder.  Suggested contributions are welcome as PRs.

## Try it in a codespace

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/jl-0/tetracorder-lite/tree/codespace-demo?quickstart=1)

A guided walkthrough: run Tetracorder over a 300x150 window of a real EMIT L2A
scene, one step at a time, and see the mineral maps at the end. No install, no
configuration; about ten minutes of which nine are the run itself.

The codespace opens on a terminal and does nothing until you run
`.devcontainer/get-started.sh`, which takes each step on request and resumes correctly if
you stop and restart the codespace. See
[`.devcontainer/README.md`](.devcontainer/README.md) for how it is put together
and how to point it at a different scene.

## Docker or Podman

Either works. Podman implements the same command-line interface, so every
example below runs unchanged under `podman` — substitute the command name and
nothing else. The only additions Podman ever needs are volume suffixes on
SELinux systems, covered below.

**Install** — follow the official instructions, they change more often than this
file does:

| | Docker | Podman |
|---|---|---|
| Windows | [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/) | [Podman Desktop](https://podman-desktop.io/docs/installation/windows-install) |
| macOS | [Docker Desktop](https://docs.docker.com/desktop/install/mac-install/) | [Podman Desktop](https://podman-desktop.io/docs/installation/macos-install) |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) | [Podman](https://podman.io/docs/installation#installing-on-linux) |

### Windows: use WSL 2

Both engines run Linux containers on Windows through a WSL 2 virtual machine, so
install WSL first. From an administrator PowerShell:

```powershell
wsl --install
```

Reboot, and you have WSL 2 with Ubuntu. Then either install Docker Desktop and
turn on **Settings → Resources → WSL integration** for your distribution, or
install Podman Desktop and let it create a Podman machine for you. Either way you
end up running `docker` or `podman` from inside the WSL shell.

**Keep the data on the Linux side.** Put your scenes and output under your WSL
home (`/home/you/...`), not under `/mnt/c/...`. Bind mounts that cross into the
Windows filesystem go through a translation layer, and a run writes roughly 2,300
small rasters — the difference is not subtle. You can still reach those files from
Explorer at `\\wsl$\Ubuntu\home\you`.

Details: [WSL install](https://learn.microsoft.com/windows/wsl/install) ·
[Docker Desktop WSL 2 backend](https://docs.docker.com/desktop/wsl/) ·
[Podman on WSL](https://podman-desktop.io/docs/installation/windows-install)

### The image is amd64-only

DaVinci is distributed as an amd64 binary, so the image is built for
`linux/amd64` and nothing else. On an arm64 host — Apple Silicon, an arm64 Linux
box, Windows on ARM — you must say so explicitly or the engine refuses the image
with `no matching manifest for linux/arm64/v8`:

```
--platform=linux/amd64
```

It runs under emulation there, which is slower but works. On an amd64 host the
flag is a no-op, so the examples below carry it throughout rather than leaving
you to work out which commands need it.

### If you are using Podman

Nothing in the examples changes — `--rm`, `-v`, `-e`, `--platform` and the rest
mean the same thing to both. Two things are worth knowing anyway:

* **SELinux** (Fedora, RHEL, CentOS): add `:z` to bind mounts, as in
  `-v /path/to/input:/data:z`. Without it the container cannot read the mount.
  This is the one flag Docker has no equivalent for.
* **Rootless**: files written to `/output` come back owned by you, not by root,
  which is usually what you want.

## Quick start

The pipeline is driven by a YAML config (see [`config.yml`](config.yml)). Mount your
data and output directories, then point `tetrapy run` at the config:

```sh
docker run --rm --platform=linux/amd64 \
  -v /path/to/input:/data \
  -v /path/to/output:/output \
  tetracorder-lite \
  tetrapy run config.yml \
    --data.rfl /data/emit20230728t214153_rfl \
    --data.rfluncert /data/emit20230728t214153_uncert
```

Every config value can be overridden on the command line with dotted `--key value`
flags (see [Overriding config on the CLI](#overriding-config-on-the-cli)).

## Building the container

```sh
docker build --platform=linux/amd64 -f Containerfile -t tetracorder-lite .
```

The image compiles specpr + Tetracorder (Fortran/ratfor), installs DaVinci, and
sets up the `tetrapy` Python environment via pixi. Building on an arm64 host is
emulated and takes considerably longer than the native build; if you only want to
run the pipeline, pull a published image instead of building one.

## The pipeline

`tetrapy run <config.yml>` executes an ordered pipeline. Each stage has an `enabled`
flag in the config, so you can run any subset:

| Stage | Config key | What it does |
|---|---|---|
| Convolve | `convolve` | Convolve the reference + research master libraries onto the scene's instrument grid and integrate them into tetracorder |
| Setup | `setup` | Configure a tetracorder run (`cmd-setup-tetrun`) |
| Tetrun | `tetrun` | Execute the configured run (`cmd.runtet`) |
| Aggregate | `aggregate` | Aggregate tetracorder outputs into L2B mineral/uncertainty products |

After Setup initializes the output directory, the resolved config is written to
`{output}/config.yml` for provenance.

## Configuration

The config is a nested YAML file. Top-level keys hold shared values (`output`,
`data`, `tetracorder`); the remaining keys are the pipeline stages, each gated by its
own `enabled` flag. Paths refer to locations **inside the container**.

```yaml
output: /output/tetracorder      # Must not exist unless setup.autoremove is True
data:
  rfl:       /data/rfl            # Reflectance data (not the .hdr)
  rfluncert: /data/rfluncert      # Reflectance uncertainty data (not the .hdr)
tetracorder:
  version: 6.00a
  mode:    cube                   # "cube" or "singlespectrum"

convolve:
  enabled:   True
  reflib:    /root/tetracorder/sl1/usgs/library06.conv/splib06b   # unconvolved reference master
  reslib:    /root/tetracorder/sl1/usgs/library06.conv/sprlb06b   # unconvolved research master
  output:    /conv/
  integrate: True                 # Wire the convolved libraries into tetracorder
  name:      ${setup.sensor}       # Interpolated from setup.sensor

setup:
  enabled:    True
  autoremove: True                # Remove output dir first (setup requires it absent)
  sensor:     tetrapy
  geology:    True
  args:       ["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"]

tetrun:
  enabled: False
  args:    ["band", "20", "gif"]

aggregate:
  enabled:     False
  tetracorder: ${output}
  output:      ${output}/l2b/
  reflib:      ${convolve.output}/reflib.envi
  reslib:      ${convolve.output}/reslib.envi
  output_as:   ["nc", "tif"]      # Product formats: NetCDF and/or GeoTIFF

emit_fmt:                         # TODO — not yet implemented
  enabled: False
```

### Interpolation (`${...}`)

Values may reference other parts of the config with `${...}` syntax, resolved when
the config is loaded:

- `${output}` — absolute reference from the top of the config (e.g. `aggregate.tetracorder`).
- `${convolve.output}` — dotted path into a subsection.
- `${.key}` — a **relative** reference, resolved within the same subsection.

For example, `name: ${setup.sensor}` in the `convolve` block reuses whatever
`setup.sensor` is set to, so the convolved libraries and the tetracorder integration
stay in sync.

### Overriding config on the CLI

Any config value can be overridden without editing the file, using dotted `--key`
flags after the config path. Values are parsed as Python literals (so lists, numbers,
and booleans work), falling back to a string if parsing fails:

```sh
tetrapy run config.yml \
  --output /output/run2 \
  --data.rfl /data/scene_rfl \
  --tetrun.enabled True \
  --tetrun.args '["band", 10, "gif"]'
```

You can also load and run just one subsection with `-s/--section`.

## Commands

The container entrypoint is `tetrapy`. `run` is the usual entry point; the individual
stages are also exposed as standalone subcommands for debugging or partial runs:

| Command | Description |
|---|---|
| `run` | Execute the full pipeline from a YAML config (the primary interface) |
| `convolve` | Convolve + integrate the spectral libraries for a scene's grid |
| `setup` | Configure a tetracorder run only (`cmd-setup-tetrun`) |
| `tetrun` | Execute a previously-configured run (`cmd.runtet`) |
| `aggregate` | Aggregate tetracorder outputs into L2B mineral/uncertainty products |
| `goc` | Convert group outputs to EMIT L2B NetCDF format |

Use `--help` on any command for full options:

```sh
docker run --rm --platform=linux/amd64 tetracorder-lite tetrapy --help
docker run --rm --platform=linux/amd64 tetracorder-lite tetrapy run --help
```

## Volume contract

| Mount point | Purpose |
|---|---|
| `/data` | Input: reflectance file (ENVI format with `.hdr`) — supplies the target grid |
| `/output` | Output: tetracorder results and aggregated L2B products |

The unconvolved master libraries (`splib06b`, `sprlb06b`; ~24 MB total) are **baked
into the image** at `/root/tetracorder/sl1/usgs/library06.conv/`, so a normal run only
needs the scene mounted at `/data`. The `convolve` stage reads its target
wavelength/FWHM grid from the reflectance ENVI header (`${data.rfl}.hdr`), so the
convolved library self-consistently matches the scene.

## Convolved spectral library

Tetracorder matches observed reflectance against a spectral library convolved to the
instrument's channels. When EMIT gets a new calibration (new wavelengths/FWHM), the
library must be regenerated. The `convolve` stage does this in **pure Python** — no
previously-convolved library is needed as a template. It builds the reference
(`splib06b`) and research (`sprlb06b`) libraries by Gaussian-convolving each master
spectrum from its native grid onto the target EMIT grid (with native-FWHM quadrature
correction, matching the USGS Fortran math), writing valid specpr libraries plus ENVI
exports for the aggregator.

When `integrate: True`, the convolved libraries are wired into the tetracorder command
tree (restart, color, disable, datasets, and deleted-channels files) under
`name`, so the subsequent Setup/Tetrun stages use them.

See [`docs/convolved-library-build.md`](docs/convolved-library-build.md) for the full
technical reference on the convolution.

## Development

The Python CLI lives in `tetrapy/`. Managed with [pixi](https://pixi.sh):

```sh
pixi install
pixi run tetrapy --help
```

The build context is filtered by two files that must be changed together:
`.dockerignore`, which only Docker reads, and `.containerignore`, which Podman
prefers. If either drifts, that engine ships the whole working tree — including
any scenes downloaded under `in/` — into the context and bakes it into the image.

## Project structure

```
tetracorder-lite/
  Containerfile          # container build (specpr + tetracorder + pixi/tetrapy)
  .dockerignore          # build context filter -- docker reads this one
  .containerignore       #   the same list for podman; keep the two in step
  pyproject.toml         # Python project config (pixi workspace)
  config.yml             # pipeline configuration consumed by `tetrapy run`
  tetrapy/               # Python CLI
    __main__.py          #   click CLI entrypoint (run + per-stage commands)
    config.py            #   YAML config loader, CLI patching, ${...} interpolation
    tetra.py             #   tetracorder setup/run + library convolution & integration
    convolve.py          #   recipe-driven convolved-library builder (pure Python)
    aggregate.py         #   L2B mineral/uncertainty product aggregation
    tetracorder.py       #   expert-system command-file decoder
    conv/                #   specpr / ENVI convolution internals
    templates/           #   tetracorder integration file templates
    data/                #   mineral grouping matrices
  tetracorder/           # vendored tetracorder + specpr source tree
  docs/                  # technical documentation
```
