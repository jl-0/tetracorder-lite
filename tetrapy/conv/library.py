"""Build a complete convolved specpr library directly from a master library.

No convolution recipe is needed: everything the USGS ``.cmds`` recipe used to
supply is already present in each master spectrum's own header --

    recipe ``recnum``  == the spectrum record itself (walked in record order)
    recipe ``inwave``  == the header's ``irwav`` (native wavelength record)
    recipe ``inres``   == the "Bandpass (FWHM)" record paired to that wavelength
                          record by matching channel count (``itchan``)
    recipe ``title``   == the header's ``ititl``

so the recipe was fully redundant with the master.  This module discovers the
spectra, pairs each with its native wave/FWHM grids, convolves onto the target
grid, and writes a specpr library reproducing the shipped ``s06emitc`` layout.

Reproduces the record layout of the shipped ``s06emitc``:

    rec 0        ASCII label
    rec 1-5      text banner records
    rec 6-7      output wavelengths (microns)   <- target grid
    rec 8-11     padding
    rec 12-13    output resolution (FWHM, microns)
    rec 14-17    padding
    rec 18-19    channel-number reference spectrum
    rec 20-29    padding
    rec 30..     one 6-record block per spectrum:
                     head + continuation (285 ch) + 4 text pads

Record numbering is significant -- Tetracorder fit-scripts reference the library by
absolute record number -- so every spectrum occupies a fixed slot in master order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import convolve
from .envi import TargetGrid, read_grid
from .specpr import (DELETED, Record, SpecprFile, SpecprWriter, default_label,
                     text_pad_record)

USERNM = "tetracnv"
WAVE_RECNO = 6           # output wavelength grid record number
RES_RECNO = 12           # output resolution grid record number
FIRST_SPECTRUM = 30      # first convolved-spectrum record number
PADS_PER_SPECTRUM = 4    # trailing [sppad] records after each spectrum

# master header records whose title starts with one of these are grid records
# (wavelength / bandpass sets), not spectra
GRID_TITLE_PREFIXES = ("Wavelengths", "Bandpass")


@dataclass
class BuildStats:
    n_spectra: int
    n_placeholders: int
    n_records: int


def _title_tag(master_title: str, family: str) -> str:
    """Derive the output title ``<name> <family>=<tag>`` from a master title.

    Master titles look like ``Acmite NMNH133746 Pyroxene   W1R1Ba AREF``.  The tag
    is the trailing lowercase/underscore run of the code token (the second-to-last
    whitespace token): ``W1R1Ba`` -> ``a``, ``W9R4Nbbb`` -> ``bbb``,
    ``W5R4Nbb_`` -> ``bb_``.  The spectrum name is the remaining tokens, whitespace
    collapsed.  Titles already carrying ``<family>=`` are passed through unchanged.
    """
    if f"{family}=" in master_title:
        return master_title
    toks = master_title.split()
    if len(toks) < 2:
        return master_title
    code = toks[-2]
    upper_positions = [i for i, ch in enumerate(code) if ch.isupper()]
    tag = code[upper_positions[-1] + 1:] if upper_positions else code
    name = " ".join(toks[:-2])
    return f"{name} {family}={tag}"


def _fit_title(text: str) -> str:
    return text[:40].ljust(40)


def _channel_numbers(nbands: int) -> np.ndarray:
    return np.arange(1, nbands + 1, dtype=np.float32)


@dataclass
class MasterIndex:
    """Discovered structure of a master library: spectra and native grids."""

    spectra: list[int]                 # record numbers of spectra, in master order
    bandpass_for_wave: dict[int, int]  # wavelength record -> paired bandpass record


def index_master(master: SpecprFile) -> MasterIndex:
    """Scan a master library, separating spectra from wavelength/bandpass grids.

    Grid records are recognised by their title prefix ("Wavelengths"/"Bandpass").
    Each wavelength record is paired with the bandpass record that shares its
    channel count, reproducing the recipe's ``inwave``/``inres`` pairing.
    """
    wave_recs: list[int] = []
    band_recs: list[int] = []
    spectra: list[int] = []

    for i in range(1, master.nrecords):
        rec = master.record(i)
        if rec.rtype != 0:
            continue                    # only data headers start a record group
        title = rec.title.rstrip()
        if title.startswith("Wavelengths"):
            wave_recs.append(i)
        elif title.startswith("Bandpass"):
            band_recs.append(i)
        else:
            spectra.append(i)

    band_by_itchan: dict[int, int] = {}
    for b in band_recs:
        band_by_itchan[master.record(b).itchan] = b

    bandpass_for_wave: dict[int, int] = {}
    for w in wave_recs:
        itchan = master.record(w).itchan
        if itchan in band_by_itchan:
            bandpass_for_wave[w] = band_by_itchan[itchan]

    return MasterIndex(spectra=spectra, bandpass_for_wave=bandpass_for_wave)


def _write_header_block(writer: SpecprWriter, grid: TargetGrid,
                        family: str) -> None:
    """Emit records 1-29: banner, output wave/res grids, channel-number spectrum."""
    nb = grid.nbands

    # rec 1-5: text banner
    writer.append(text_pad_record())
    writer.append(text_pad_record())
    writer.append(text_pad_record())
    writer.append(text_pad_record())
    writer.append(text_pad_record())

    # rec 6-7: output wavelengths (microns)
    assert writer.next_recno == WAVE_RECNO, writer.next_recno
    writer.append_spectrum(
        icflag_head=16, title=_fit_title(f"Wavelengths in microns {nb} ch"),
        usernm=USERNM, itchan=nb, irwav=WAVE_RECNO, irespt=RES_RECNO,
        values=grid.wavelengths_um.astype(np.float32))
    writer.append_pads(4)

    # rec 12-13: output resolution (FWHM, microns)
    assert writer.next_recno == RES_RECNO, writer.next_recno
    writer.append_spectrum(
        icflag_head=16, title=_fit_title(f"Resolution in microns {nb} ch"),
        usernm=USERNM, itchan=nb, irwav=WAVE_RECNO, irespt=RES_RECNO,
        values=grid.fwhm_um.astype(np.float32))
    writer.append_pads(4)

    # rec 18-19: channel-number reference spectrum
    assert writer.next_recno == 18, writer.next_recno
    writer.append_spectrum(
        icflag_head=16, title=_fit_title(f"Data value = channel number ({nb} ch)"),
        usernm=USERNM, itchan=nb, irwav=WAVE_RECNO, irespt=RES_RECNO,
        values=_channel_numbers(nb))
    writer.append_pads(10)

    assert writer.next_recno == FIRST_SPECTRUM, writer.next_recno


def _deleted_spectrum(nbands: int) -> np.ndarray:
    return np.full(nbands, DELETED, dtype=np.float32)


def build_library(master_path, hdr_path, out_path, *,
                  family: str = "s06emitc", log=print) -> BuildStats:
    """Convolve every spectrum in a master library onto the target grid and write a
    complete specpr library to ``out_path``.

    Parameters
    ----------
    master_path : the unconvolved master specpr library (``splib06b`` / ``sprlb06b``)
    hdr_path    : ENVI ``.hdr`` supplying the target wavelength/FWHM grid
    out_path    : output specpr library path
    family      : output title family tag (``s06emitc`` / ``r06emitc``)
    """
    grid = read_grid(hdr_path)
    master = SpecprFile.open(master_path)
    index = index_master(master)
    nb = grid.nbands

    log(f"[{family}] grid: {nb} bands "
        f"{grid.wavelengths_um[0]*1000:.1f}-{grid.wavelengths_um[-1]*1000:.1f} nm; "
        f"master: {index.spectra and len(index.spectra)} spectra, "
        f"{len(index.bandpass_for_wave)} native grids")

    writer = SpecprWriter(default_label())
    _write_header_block(writer, grid, family)

    grid_cache: dict[int, np.ndarray] = {}

    def native(recno: int) -> np.ndarray:
        if recno not in grid_cache:
            grid_cache[recno] = master.read_spectrum(recno).astype(np.float64)
        return grid_cache[recno]

    n_placeholders = 0
    for idx, recno in enumerate(index.spectra):
        head = master.record(recno)
        title = _fit_title(_title_tag(head.title, family))
        inwave = head.irwav
        inres = index.bandpass_for_wave.get(inwave)

        # A spectrum whose native grids can't be resolved becomes a deleted-data
        # placeholder so downstream record numbering is preserved.
        try:
            if inres is None:
                raise ValueError(f"no bandpass grid paired to wave record {inwave}")
            in_wave = native(inwave)
            in_fwhm = native(inres)
            in_spec = master.read_spectrum(recno).astype(np.float64)
        except (IndexError, ValueError) as exc:
            log(f"[{family}] spectrum {idx} rec{recno} title={head.title.rstrip()!r}: "
                f"cannot read native grids ({exc}) -> placeholder")
            writer.append_spectrum(
                icflag_head=16, title=title, usernm=USERNM, itchan=nb,
                irwav=WAVE_RECNO, irespt=RES_RECNO, values=_deleted_spectrum(nb))
            writer.append_pads(PADS_PER_SPECTRUM)
            n_placeholders += 1
            continue

        convolved = convolve.convolve_spectrum(
            in_wave, in_fwhm, in_spec, grid.wavelengths_um, grid.fwhm_um)

        icflag_head = head.icflag & ~3    # keep master's high flag bits
        writer.append_spectrum(
            icflag_head=icflag_head, title=title, usernm=USERNM, itchan=nb,
            irwav=WAVE_RECNO, irespt=RES_RECNO, values=convolved,
            ihist=f"f17: convolved r{recno} Gaussian", template=head)
        writer.append_pads(PADS_PER_SPECTRUM)

    writer.write(out_path)
    stats = BuildStats(n_spectra=len(index.spectra), n_placeholders=n_placeholders,
                       n_records=writer.next_recno)
    log(f"[{family}] wrote {out_path}: {stats.n_records} records "
        f"({stats.n_spectra} spectra, {stats.n_placeholders} placeholders)")
    return stats
