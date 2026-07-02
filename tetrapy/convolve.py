"""Build a convolved spectral library for a given instrument wavelength/FWHM.

Tetracorder matches against a spectral library that has been *convolved* to the
instrument's channels. When a new calibration epoch appears, the convolved
library must be regenerated. The wavelength + FWHM that define the convolution
are carried in the ENVI header of any EMIT product (reflectance is ideal — it is
tetracorder's own input, so the values self-consistently match the scene).

This module:
  1. reads `wavelength` and `fwhm` out of an ENVI `.hdr` (nm -> microns), and
  2. drives the USGS `AAA.make.new.instrument.convolved.spectral.library.sh`
     recipe against a mounted, unconvolved master library (`splib06b`),
     writing the convolved library to an output path.

The master library is mounted at runtime, never baked into the image — see
`docs/convolved-library-build.md` for the volume contract.
"""

import re
import shutil
import subprocess
from pathlib import Path

# specpr convolution scripts live here in the container tree.
LIBRARY06_CONV = Path("/root/tetracorder/sl1/usgs/library06.conv")

# The unconvolved master library the scripts read from their CWD.
MASTER_LIB = "splib06b"


def _resolve_hdr(path):
    """Return the ENVI `.hdr` for a product path (accepts the .hdr or the cube)."""
    p = Path(path)
    if p.suffix == ".hdr":
        return p
    # ENVI allows either `<file>.hdr` or `<stem>.hdr`
    for cand in (p.with_suffix(p.suffix + ".hdr"), p.with_suffix(".hdr")):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"No ENVI header found for {path}")


def _parse_array(text, key):
    """Pull `key = { a , b , ... }` out of an ENVI header as a list of floats."""
    m = re.search(rf"^{key}\s*=\s*\{{(.*?)\}}", text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError(f"ENVI header has no '{key}' field")
    return [float(v) for v in m.group(1).replace("\n", " ").split(",") if v.strip()]


def read_wavelengths_fwhm(hdr_path):
    """Read (wavelengths, fwhm) in **microns** from an ENVI header.

    \b
    Parameters
    ----------
    hdr_path : str | Path
        Path to the ENVI `.hdr` (or the product it describes).

    \b
    Returns
    -------
    (list[float], list[float])
        Wavelengths and FWHM, converted to microns.
    """
    hdr = _resolve_hdr(hdr_path)
    text = hdr.read_text()

    waves = _parse_array(text, "wavelength")
    fwhm = _parse_array(text, "fwhm")
    if len(waves) != len(fwhm):
        raise ValueError(f"wavelength ({len(waves)}) and fwhm ({len(fwhm)}) length mismatch")

    # EMIT headers are in nanometers; convolution scripts want microns.
    units = re.search(r"^wavelength units\s*=\s*(\S+)", text, re.IGNORECASE | re.MULTILINE)
    unit = (units.group(1).lower() if units else "nanometers")
    if unit.startswith("nan") or unit.startswith("nm"):
        waves = [w / 1000.0 for w in waves]
        fwhm = [f / 1000.0 for f in fwhm]
    elif not (unit.startswith("mic") or unit.startswith("um") or unit.startswith("µ")):
        raise ValueError(f"Unexpected wavelength units '{unit}' — expected nm or microns")

    return waves, fwhm


def write_convol_inputs(
    file="/data/rfl",
    waves_out="waves.txt",
    resol_out="resol.txt",
    **_,
):
    """Write the specpr convolution inputs from an EMIT ENVI header.

    Reads `wavelength`/`fwhm` from the header and writes two single-column ascii
    files in microns, as required by the USGS convolution scripts.

    \b
    Parameters
    ----------
    file : str, default="/data/rfl"
        EMIT product (reflectance recommended) or its `.hdr`.
    waves_out : str, default="waves.txt"
        Output wavelength file (microns, one value per line).
    resol_out : str, default="resol.txt"
        Output FWHM/resolution file (microns, one value per line).

    \b
    Returns
    -------
    int
        Number of channels written.
    """
    waves, fwhm = read_wavelengths_fwhm(file)

    Path(waves_out).write_text("".join(f"{w:.8f}\n" for w in waves))
    Path(resol_out).write_text("".join(f"{f:.8f}\n" for f in fwhm))

    print(f"Wrote {len(waves)} channels -> {waves_out}, {resol_out} (microns)")
    return len(waves)


def build_convolved_library(
    file="/data/rfl",
    library_path="/data/splib06b",
    output="/output/library",
    name="semcalx",
    version="a",
    title="EMIT",
    fwhm_record="12",
    conv_dir=LIBRARY06_CONV,
    **_,
):
    """Regenerate a convolved spectral library for an instrument calibration.

    Reads the instrument wavelength/FWHM from an EMIT ENVI header, then runs the
    USGS `AAA.make.new.instrument.convolved.spectral.library.sh` recipe against a
    mounted unconvolved master library, writing the convolved library to `output`.

    \b
    Parameters
    ----------
    file : str, default="/data/rfl"
        EMIT product (reflectance recommended) or its `.hdr` — source of wl/FWHM.
    library_path : str, default="/data/splib06b"
        Mounted unconvolved master library (splib06b) to convolve.
    output : str, default="/output/library"
        Directory the convolved library is written to.
    name : str, default="semcalx"
        7-char specpr library name (e.g. `sem2507`).
    version : str, default="a"
        1-char library version.
    title : str, default="EMIT"
        Title keyword passed to the convolution.
    fwhm_record : str, default="12"
        specpr FWHM record number.
    conv_dir : Path
        library06.conv directory holding the convolution scripts.
    """
    conv_dir = Path(conv_dir)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    # Stage the mounted master library into the script's working dir (it reads it from CWD).
    master = conv_dir / MASTER_LIB
    if not master.exists():
        src = Path(library_path)
        if not src.exists():
            raise FileNotFoundError(f"Master library not found at {library_path}")
        master.symlink_to(src.resolve())

    # 1. wavelength/FWHM -> waves.txt / resol.txt (microns), in the working dir.
    nchan = write_convol_inputs(
        file=file,
        waves_out=conv_dir / "waves.txt",
        resol_out=conv_dir / "resol.txt",
    )

    lib = f"{name}{version}"

    # 2. run the USGS convolution recipe (noX = headless, ascii plots).
    cmd = [
        "./AAA.make.new.instrument.convolved.spectral.library.sh",
        name, version, str(nchan), title, fwhm_record,
        f"Convolved {title} {nchan} ch library ",
        f"Wavelengths in microns {nchan} ch {lib} ",
        f"Resolution in microns {nchan} ch {lib} ",
        "-waves", "waves.txt", "-fwhm", "resol.txt", "noX",
    ]
    subprocess.run(cmd, cwd=conv_dir, check=True)

    # 3. publish the convolved library (+ its restart file) to the output path.
    produced = conv_dir / lib
    if not produced.exists():
        raise RuntimeError(f"convolution did not produce {produced}")
    shutil.copy2(produced, out / lib)
    restart = conv_dir / "restartfiles" / f"r.{lib}"
    if restart.exists():
        shutil.copy2(restart, out / f"r.{lib}")

    print(f"Convolved library {lib} ({nchan} ch) -> {out / lib}")
    return out / lib
