"""
Build a complete convolved specpr library from an unconvolved master.

No convolution "recipe" is needed: everything the USGS ``.cmds`` recipe supplied is
already in each master spectrum's own header. Every data record whose title is not a
``Wavelengths``/``Bandpass`` grid is a spectrum; its native wavelength and bandpass
grids are the master grid records that share its channel count. This module discovers
the spectra, convolves each onto the target grid read from a scene's ENVI header, and
writes a specpr library reproducing the shipped ``s06emitc`` layout::

    rec 0        ASCII label
    rec 1-5      text banner
    rec 6-11     output wavelengths (microns) + padding
    rec 12-17    output resolution (FWHM, microns) + padding
    rec 18-29    channel-number reference spectrum + padding
    rec 30..     one 6-record block per spectrum (head + continuation + 4 pads)

Record numbering is significant -- Tetracorder's fit scripts reference the library by
absolute record number -- so every spectrum occupies a fixed slot in master order and
spectra whose native grids are missing become deleted-data placeholders rather than
being dropped.
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Union

import numpy as np

from tetrapy.conv.convolve import Convolver
from tetrapy.conv.specpr import DELETED, SpecprFile, SpecprWriter


Logger = logging.getLogger(__name__)

NM_PER_UM = 1000.0
USERNM = "tetracnv"

WAVE_RECNO = 6           # output wavelength grid
RES_RECNO = 12           # output resolution grid
CHAN_RECNO = 18          # channel-number reference spectrum
FIRST_SPECTRUM = 30      # first convolved spectrum
PADS_PER_SPECTRUM = 4    # trailing padding records after each spectrum

# Family tag written into each convolved title, keyed by master library filename.
FAMILIES = {"splib06b": "s06tetra", "sprlb06b": "r06tetra"}


@dataclass
class TargetGrid:
    """Output wavelengths and FWHM (both microns) for the convolved library."""

    wavelengths: np.ndarray
    fwhm: np.ndarray

    @property
    def nbands(self) -> int:
        return self.wavelengths.size


def read_grid(rfl: Union[str, Path]) -> TargetGrid:
    """
    Read the target wavelength/FWHM grid from a scene's ENVI header.

    ``rfl`` is the reflectance data path; its companion ``.hdr`` supplies the grid.
    EMIT headers store nanometers, so values are converted to microns unless the
    header declares micron units.
    """
    hdr = Path(rfl)
    if hdr.suffix != ".hdr":
        hdr = hdr.with_suffix(hdr.suffix + ".hdr")
    text = hdr.read_text(errors="replace")

    wavelengths = _parse_vector(text, "wavelength")
    fwhm = _parse_vector(text, "fwhm")
    if wavelengths.size != fwhm.size:
        raise ValueError(f"{hdr}: wavelength/fwhm length mismatch")

    units = re.search(r"^\s*wavelength units\s*=\s*(\S+)", text, re.IGNORECASE | re.MULTILINE)
    is_nm = units is None or units.group(1).lower().startswith(("nan", "nm"))
    scale = NM_PER_UM if is_nm else 1.0

    return TargetGrid(wavelengths=wavelengths / scale, fwhm=fwhm / scale)


def _parse_vector(text: str, key: str) -> np.ndarray:
    """Extract an ENVI ``key = { a, b, ... }`` numeric vector."""
    m = re.search(rf"^\s*{key}\s*=\s*\{{(.*?)\}}", text,
                  re.IGNORECASE | re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError(f"ENVI header has no '{key}' field")
    return np.array([float(p) for p in m.group(1).split(",") if p.strip()], dtype=np.float64)


@dataclass
class MasterIndex:
    """The spectra of a master library and their native grid records."""

    spectra: List[int]                    # spectrum record numbers, in master order
    wave_by_channels: Dict[int, int]      # channel count -> wavelength record
    band_by_channels: Dict[int, int]      # channel count -> bandpass record


def index_master(master: SpecprFile) -> MasterIndex:
    """
    Scan a master library, separating spectra from wavelength/bandpass grids.

    Grid records are recognised by their title prefix and keyed by channel count so
    each spectrum can be paired with the native grids it shares a channel count with
    (matching the recipe's ``inwave``/``inres`` selection).
    """
    spectra: List[int] = []
    wave_by_channels: Dict[int, int] = {}
    band_by_channels: Dict[int, int] = {}

    for recno in master.spectra():
        rec = master.record(recno)
        title = rec.title
        if title.startswith("Wavelengths"):
            wave_by_channels.setdefault(rec.itchan, recno)
        elif title.startswith("Bandpass"):
            band_by_channels.setdefault(rec.itchan, recno)
        else:
            spectra.append(recno)

    return MasterIndex(spectra, wave_by_channels, band_by_channels)


def _title(text: str) -> str:
    return text[:40].ljust(40)


def _convolved_title(master_title: str, family: str) -> str:
    """
    Derive the convolved title ``<name> <family>=<tag>`` from a master title.

    Master titles look like ``Acmite NMNH133746 Pyroxene   W1R1Ba AREF``: the tag is
    the trailing lowercase/underscore run of the code token (second-to-last token),
    so ``W1R1Ba`` -> ``a`` and ``W5R4N___`` -> ``___``.
    """
    tokens = master_title.split()
    if len(tokens) < 2:
        return master_title
    code = tokens[-2]
    uppers = [i for i, ch in enumerate(code) if ch.isupper()]
    tag = code[uppers[-1] + 1:] if uppers else code
    return f"{' '.join(tokens[:-2])} {family}={tag}"


def _write_grid_records(writer: SpecprWriter, grid: TargetGrid) -> None:
    """Emit records 6-29: the output wavelength/resolution/channel-number grids."""
    nb = grid.nbands

    def emit(recno: int, title: str, values: np.ndarray, pads: int) -> None:
        assert writer.next_recno == recno, (writer.next_recno, recno)
        writer.append_spectrum(
            values=values.astype(np.float32), title=_title(title), itchan=nb,
            irwav=WAVE_RECNO, irespt=RES_RECNO, usernm=USERNM)
        writer.append_pads(pads)

    emit(WAVE_RECNO, f"Wavelengths in microns {nb} ch", grid.wavelengths, 4)
    emit(RES_RECNO, f"Resolution  in microns {nb} ch", grid.fwhm, 4)
    emit(CHAN_RECNO, f"Data value = channel number ({nb} ch)",
         np.arange(1, nb + 1, dtype=np.float32), 10)
    assert writer.next_recno == FIRST_SPECTRUM, writer.next_recno


def build_library(
    master_path: Union[str, Path],
    out_path: Union[str, Path],
    rfl: Union[str, Path],
) -> None:
    """
    Convolve every spectrum in a master library onto a scene grid and write it out.

    Parameters
    ----------
    master_path : str or Path
        Unconvolved master specpr library (``splib06b`` / ``sprlb06b``).
    out_path : str or Path
        Destination path for the convolved specpr library.
    rfl : str or Path
        Scene reflectance data path; its ``.hdr`` supplies the target grid.
    """
    grid = read_grid(rfl)
    master = SpecprFile.open(master_path)
    index = index_master(master)
    convolver = Convolver(grid.wavelengths, grid.fwhm)
    family = FAMILIES.get(Path(master_path).name, Path(out_path).name)

    Logger.debug(f"[{family}] grid: {grid.nbands} bands "
        f"{grid.wavelengths[0] * 1000:.1f}-{grid.wavelengths[-1] * 1000:.1f} nm; "
        f"master: {len(index.spectra)} spectra")

    writer = SpecprWriter()
    writer.append_pads(5)                # rec 1-5: text banner
    _write_grid_records(writer, grid)

    nb = grid.nbands
    placeholders = 0
    for recno in index.spectra:
        head = master.record(recno)
        title = _title(_convolved_title(head.title, family))
        wave_rec = index.wave_by_channels.get(head.itchan)
        band_rec = index.band_by_channels.get(head.itchan)

        if wave_rec is None or band_rec is None:
            # Preserve record numbering with a deleted-data placeholder.
            Logger.debug(f"[{family}] rec{recno} {head.title!r}: no native grid for "
                f"{head.itchan} ch -> placeholder")
            writer.append_spectrum(
                values=np.full(nb, DELETED, dtype=np.float32), title=title,
                itchan=nb, irwav=WAVE_RECNO, irespt=RES_RECNO, usernm=USERNM)
            placeholders += 1
        else:
            convolved = convolver.convolve(
                master.read_spectrum(wave_rec),
                master.read_spectrum(band_rec),
                master.read_spectrum(recno))
            writer.append_spectrum(
                values=convolved, title=title, itchan=nb, icflag=head.icflag,
                irwav=WAVE_RECNO, irespt=RES_RECNO, usernm=USERNM, template=head)
        writer.append_pads(PADS_PER_SPECTRUM)

    writer.write(out_path)
    Logger.debug(f"[{family}] wrote {out_path}: {writer.next_recno} records "
        f"({len(index.spectra)} spectra, {placeholders} placeholders)")
