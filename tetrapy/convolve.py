"""Convolve a base USGS spectral library onto an instrument grid — pure Python.

Streamlined, single-purpose reimplementation of the USGS specpr convolution.
Given three things

  * a **base** (unconvolved) specpr library — ``splib06b`` / ``sprlb06b``,
  * a scene **reflectance ENVI header** supplying the target wavelength/FWHM grid,
  * an **output** path,

it writes a convolved specpr library ready for Tetracorder. No external
convolution *recipe* is required: the per-spectrum native wavelength and
resolution grids are read straight from each record's own specpr header
(``irwav`` / ``irespt`` pointers), so the library is convolved against exactly
the vintage present on disk.

Pipeline:

  1. read the target grid from the reflectance header,
  2. walk the base library, extracting one recipe row per mineral spectrum
     (its record, native wavelength grid, native resolution grid),
  3. Gaussian-convolve each spectrum from its native grid+resolution onto the
     target grid (with a native-FWHM quadrature correction), and
  4. write a valid specpr library (30-record grid preamble + one fixed-size
     block per spectrum) plus an ENVI sidecar for the L2B aggregator.

The convolution math is the validated port from :mod:`tetrapy.convolve`
(median per-spectrum RMS ~4.4e-5 vs. the shipped ``s06emitc``). The specpr
layout is the 1536-byte big-endian record documented in
``specpr/specpr-format-2,3/specpr-format-v2.txt``.
"""

import re
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from numpy.typing import NDArray

RECLEN = 1536
HEAD_FMT = ">i40s8s16i60s74s74s74s74s6i260f"   # case 0: data-start (256 chan)
CONT_FMT = ">i383f"                            # case 1: data-continuation (383 chan)
IGNORE = -1.23e34                              # specpr bad/deleted-channel sentinel

# byte offsets we write directly
OFF_ITCHAN = 80
OFF_DATA = 512

# label1 field indices into a HEAD_FMT unpack tuple (t[0] icflag, t[1] title,
# t[3..18] the 16 int*4 header words); see specpr format + spprint.py.
IDX_ITCHAN = 10   # channel count
IDX_IRWAV = 15    # wavelength-record pointer (spectrum's native grid)
IDX_IRESPT = 16   # resolution-record pointer (spectrum's native FWHM)


# --------------------------------------------------------------------------- I/O
def load(path: str) -> List[bytes]:
    """Read a specpr file into a list of 1536-byte records."""
    d = Path(path).read_bytes()
    return [d[i * RECLEN:(i + 1) * RECLEN] for i in range(len(d) // RECLEN)]


def save(path: str, records: List[bytes]) -> None:
    """Write a list of 1536-byte records to a specpr file."""
    Path(path).write_bytes(b"".join(records))


# ---------------------------------------------------------------- record fields
def _is_data_start(rec: bytes) -> bool:
    """True if ``rec`` is a data-start record (icflag & 3 == 0)."""
    return (struct.unpack(">i", rec[:4])[0] & 3) == 0


def _title(rec: bytes) -> str:
    """ASCII title of a specpr record, trailing nulls/spaces stripped."""
    return struct.unpack(HEAD_FMT, rec)[1].decode("ascii", "ignore").rstrip("\x00 ")


def _itchan(rec: bytes) -> int:
    """Channel count (itchan) of a specpr record."""
    return struct.unpack(">i", rec[OFF_ITCHAN:OFF_ITCHAN + 4])[0]


def _header_words(rec: bytes) -> Tuple[int, int, int]:
    """Return ``(itchan, irwav, irespt)`` from a data-start record header."""
    t = struct.unpack(HEAD_FMT, rec)
    return t[IDX_ITCHAN], t[IDX_IRWAV], t[IDX_IRESPT]


def read_array(records: List[bytes], recno: int) -> NDArray[np.float64]:
    """Reassemble the full float channel array of the spectrum at ``recno``.

    Spans the data-start record plus any continuation records. Bad/deleted
    channels (``|v| >= 1e30``) are returned as NaN.
    """
    n = _itchan(records[recno])
    t = struct.unpack(HEAD_FMT, records[recno])
    out = list(t[34:34 + min(256, n)])
    c = recno + 1
    while len(out) < n:
        ct = struct.unpack(CONT_FMT, records[c])
        out += list(ct[1:1 + min(383, n - len(out))])
        c += 1
    a = np.array(out, dtype=np.float64)
    a[np.abs(a) >= 1e30] = np.nan
    return a


# ------------------------------------------------------------------- library map
def _is_setup(title: str) -> bool:
    """True for non-mineral records (wavelength/resolution/reference/text)."""
    return (title.startswith("Wavelengths") or "Bandpass" in title or "FWHM" in title
            or "Resolution" in title or "Data value" in title
            or "Digital Spectral Library" in title or title.startswith("*")
            or title in ("", ".."))


def mineral_records(records: List[bytes]) -> List[int]:
    """Ordered record numbers holding mineral spectra (setup records excluded)."""
    return [i for i, r in enumerate(records)
            if _is_data_start(r) and not _is_setup(_title(r))]


def _wavelength_resolution_records(records: List[bytes]) -> Dict[int, Optional[int]]:
    """Map each Wavelengths record to the next Bandpass/FWHM record of equal channels.

    Used to recover a spectrum's resolution grid when its stored ``irespt``
    pointer is stale (an artifact of older library edits).
    """
    waves, bands = [], []
    for i, r in enumerate(records):
        if _is_data_start(r):
            t = _title(r)
            if t.startswith("Wavelengths"):
                waves.append(i)
            elif "Bandpass" in t or "FWHM" in t:
                bands.append(i)
    out: Dict[int, Optional[int]] = {}
    for w in waves:
        wc = _itchan(records[w])
        cand = [b for b in bands if b > w and _itchan(records[b]) == wc]
        out[w] = min(cand) if cand else None
    return out


def extract_recipe(records: List[bytes]) -> List[dict]:
    """Derive the convolution recipe from a base library's own specpr headers.

    Every base-library spectrum carries its native grids as record pointers:
    ``irwav`` (wavelength grid) and ``irespt`` (resolution/FWHM grid). This walks
    the mineral spectra and returns, for each, the records to read —
    ``{inwave, inres, recnum, title}`` — where ``recnum`` is the direct index
    into ``records``.

    A stale ``irespt`` (observed pointing at a continuation record) is repaired
    by pairing the spectrum's wavelength grid with the next matching Bandpass/FWHM
    record. A spectrum is dropped only if neither its stored pointer nor that
    fallback yields a channel-matched resolution grid.
    """
    n = len(records)
    wav_to_res = _wavelength_resolution_records(records)

    def valid(idx: int, want_chan: int) -> bool:
        return (0 <= idx < n and _is_data_start(records[idx])
                and _itchan(records[idx]) == want_chan)

    rows, dropped = [], []
    for rn in mineral_records(records):
        itchan, irwav, irespt = _header_words(records[rn])

        if not valid(irwav, itchan):
            dropped.append((rn, _title(records[rn]), "no wavelength grid"))
            continue

        if not valid(irespt, itchan):
            fallback = wav_to_res.get(irwav)
            if fallback is not None and valid(fallback, itchan):
                irespt = fallback
            else:
                dropped.append((rn, _title(records[rn]), "no resolution grid"))
                continue

        rows.append({"inwave": irwav, "inres": irespt, "recnum": rn,
                     "title": _title(records[rn])})

    if dropped:
        print(f"note: skipped {len(dropped)} spectra with unusable grid pointers")
        for rn, title, why in dropped[:10]:
            print(f"      rec {rn}: {title} — {why}")
    return rows


# ------------------------------------------------------------- ENVI target grid
def read_wavelengths_fwhm(hdr_path: str) -> Tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Read (wavelengths, FWHM) in microns from a reflectance ENVI header.

    Accepts a reflectance-file path or its ``.hdr``; locates the header if needed.
    EMIT headers are in nanometers and are converted to microns automatically.
    """
    p = Path(hdr_path)
    if p.suffix != ".hdr":
        for cand in (p.with_suffix(p.suffix + ".hdr"), p.with_suffix(".hdr")):
            if cand.exists():
                p = cand
                break
    text = p.read_text()

    def arr(key):
        m = re.search(rf"^{key}\s*=\s*\{{(.*?)\}}", text, re.I | re.S | re.M)
        if not m:
            raise ValueError(f"ENVI header has no '{key}'")
        return np.array([float(v) for v in m.group(1).replace("\n", " ").split(",") if v.strip()])

    waves, fwhm = arr("wavelength"), arr("fwhm")
    units = re.search(r"^wavelength units\s*=\s*(\S+)", text, re.I | re.M)
    unit = (units.group(1).lower() if units else "nanometers")
    if unit.startswith(("nan", "nm")):
        waves, fwhm = waves / 1000.0, fwhm / 1000.0
    elif not unit.startswith(("mic", "um", "µ")):
        raise ValueError(f"unexpected wavelength units '{unit}'")
    return waves, fwhm


# --------------------------------------------------------------------- convolve
def convolve_spectrum(native_wl, native_val, native_fwhm, out_wl, out_fwhm, tlim=1e-7):
    """Convolve one native spectrum onto the target grid.

    Matches the Fortran specpr convolution (gfiles + ggauss + convol + delx):
      - Quadrature subtraction: effective FWHM = sqrt(out² - native²)
      - When out_fwhm <= native_fwhm: fall back to native_fwhm * 0.01 and snap the
        center to the nearest native channel (gfiles.r lines 284-290)
      - Channels outside the native wavelength range are deleted (gfiles.r line 300)
      - Trapezoidal channel-spacing weighting (delx)
      - Gaussian weight threshold ``tlim`` (Fortran default 1e-7)
      - Normalized convolution (nmode=1 in convol.r)
    """
    n_out = out_wl.size
    out = np.full(n_out, np.nan)

    good = (np.abs(native_val) < 1e30) & (np.abs(native_wl) < 1e30)
    if good.sum() < 2:
        return out

    good_idx = np.where(good)[0]
    gw = native_wl[good_idx]
    gv = native_val[good_idx]

    # wavmin/wavmax come from the wavelength array (all channels), so Gaussian
    # tails can reach data even when the output center sits in a bad-data region.
    wl_valid = native_wl[np.abs(native_wl) < 1e30]
    lo, hi = wl_valid.min(), wl_valid.max()

    # channel spacing (delx equivalent)
    n_good = len(good_idx)
    dx = np.zeros(n_good)
    for ki in range(n_good):
        if ki == 0:
            dx[ki] = abs(gw[1] - gw[0])
        elif ki == n_good - 1:
            dx[ki] = abs(gw[-1] - gw[-2])
        else:
            dx[ki] = abs(gw[ki + 1] - gw[ki - 1]) * 0.5

    nf = native_fwhm[good_idx] if native_fwhm is not None else None

    for i in range(n_out):
        lam = out_wl[i]
        bw = out_fwhm[i]
        if bw <= 0 or not np.isfinite(bw):
            continue
        if lam < lo or lam > hi:                 # outside native range -> deleted
            continue

        if nf is not None:                       # quadrature-subtract native res
            conind = np.argmin(np.abs(gw - lam))
            native_res = nf[conind]
            if bw <= native_res:
                eff_bw = native_res * 0.01       # Fortran fallback + snap center
                lam = gw[conind]
            else:
                eff_bw = np.sqrt(bw * bw - native_res * native_res)
        else:
            eff_bw = bw

        ax = -2.772589 / (eff_bw * eff_bw)       # ggauss: -4*ln2 / FWHM^2
        diff = gw - lam
        gauss = np.exp(ax * diff * diff)
        sel = gauss > tlim
        if sel.sum() < 1:
            continue

        xx = gauss[sel] * dx[sel]                # convol: normalized trapezoid sum
        nrmsum = np.sum(xx)
        if nrmsum > 0.0:
            out[i] = np.sum(gv[sel] * xx) / nrmsum

    return out


# ---------------------------------------------------------- specpr record build
def _text_record(title: str, icflag: int = 2) -> bytes:
    """Build a specpr text record (icflag & 3 == 2) with ``title`` in the header.

    Used for the library banner and the ``..`` spacer/padding records that keep
    absolute record numbers aligned with what Tetracorder's fit-scripts expect.
    """
    rec = bytearray(RECLEN)
    struct.pack_into(">i", rec, 0, icflag)
    rec[4:44] = title.encode("ascii", "replace")[:40].ljust(40, b" ")
    struct.pack_into(">i", rec, 56, 41)          # itxtch — cosmetic char count
    return bytes(rec)


def _data_block(shell: bytes, title: str, values: NDArray[np.float64]) -> List[bytes]:
    """Build a data-start record (+ continuations) from a cloned header ``shell``.

    ``shell`` is a real base data-start record (valid dates/flags/pointers). Title,
    ``itchan`` and the float data are overwritten; NaNs become the IGNORE sentinel.
    """
    n = len(values)
    v = np.asarray(values, dtype=np.float64).copy()
    v[~np.isfinite(v)] = IGNORE
    v = v.astype(">f4")

    head = bytearray(shell)
    head[4:44] = title.encode("ascii", "replace")[:40].ljust(40, b" ")
    struct.pack_into(">i", head, OFF_ITCHAN, n)
    k = min(256, n)
    head[OFF_DATA:OFF_DATA + k * 4] = v[:k].tobytes()
    if k < 256:                                  # zero stale data past the channels
        head[OFF_DATA + k * 4:OFF_DATA + 256 * 4] = b"\x00" * ((256 - k) * 4)
    out = [bytes(head)]

    off = k
    while off < n:
        cont = bytearray(RECLEN)
        struct.pack_into(">i", cont, 0, 1)       # icflag & 3 == 1 continuation
        take = min(383, n - off)
        cont[4:4 + take * 4] = v[off:off + take].tobytes()
        out.append(bytes(cont))
        off += take
    return out


# ------------------------------------------------------------------ entry point
def convolve_library(master: str, envi_header: str, output: str, sppad: int = 4) -> str:
    """Convolve a base spectral library onto a scene's grid and write it out.

    The single entry point: reads the target grid from ``envi_header``, extracts
    the recipe from ``master``'s own headers, convolves every mineral spectrum,
    and writes a valid specpr library to ``output`` (plus an ``{output}.envi``
    ENVI sidecar for the L2B group aggregator).

    The output has the specpr layout Tetracorder expects — a 30-record grid
    preamble (banner, then Wavelengths / Resolution / channel-number grids)
    followed by one fixed-size block per spectrum (data-start + continuation +
    ``sppad`` padding records) so absolute record numbers stay aligned.

    \b
    Parameters
    ----------
    master : str
        Base (unconvolved) specpr library — ``splib06b`` / ``sprlb06b``.
    envi_header : str
        Scene reflectance ENVI header (``.hdr``); a reflectance path is accepted.
    output : str
        Output path for the convolved specpr library.
    sppad : int, default=4
        Padding text records per spectrum (Fortran used 4); sets the record stride.

    Returns
    -------
    str
        The ``output`` path written.
    """
    mrecs = load(master)
    out_wl, out_fwhm = read_wavelengths_fwhm(envi_header)
    rows = extract_recipe(mrecs)
    if not rows:
        raise ValueError(f"no convolvable spectra found in {master}")

    n_ch = out_wl.size
    channel_axis = np.arange(1, n_ch + 1, dtype=np.float64)
    shell = mrecs[rows[0]["recnum"]]             # clone a valid header

    # ---- grid preamble (records 0-29), matching the shipped s06emitc spacing ---
    name = Path(output).name
    records = [
        _text_record(f"USGS Digital Spectral Library: {name}"),
        _text_record(f"USGS Digital Spectral Library: {name}"),
        _text_record(f"Convolved library {n_ch} ch (built by tetrapy)"),
        _text_record("*" * 40),
        _text_record("*" * 40),
        _text_record(".."),
    ]
    pds = bytearray(RECLEN)                       # PDS-style label at record 0
    pds[:52] = b"SPECPR_FS=2.0\r\nRECORD_BYTES=1536\r\nLABEL_RECORDS=1\r\n\x00\x00"[:52]
    records[0] = bytes(pds)

    def append_grid(title, values, block_end):
        for r in _data_block(shell, title, values):
            records.append(r)
        while len(records) < block_end:
            records.append(_text_record(".."))

    append_grid(f"Wavelengths in microns {n_ch} ch EMIT", out_wl, 12)
    append_grid(f"Resolution  in microns {n_ch} ch EMIT", out_fwhm, 18)
    append_grid(f"Data value = channel number ({n_ch} ch)", channel_axis, 30)
    assert len(records) == 30, f"preamble is {len(records)} records, expected 30"

    # ---- one fixed-size block per spectrum ----
    block = 2 + sppad                             # data-start + continuation + pad
    for row in rows:
        conv = convolve_spectrum(
            read_array(mrecs, row["inwave"]), read_array(mrecs, row["recnum"]),
            read_array(mrecs, row["inres"]), out_wl, out_fwhm)
        blk = _data_block(shell, row["title"], conv)
        blk += [_text_record("..") for _ in range(block - len(blk))]
        records.extend(blk)

    save(output, records)
    print(f"Wrote {len(rows)} convolved spectra ({n_ch} ch) -> {output}")
    export_envi(output, f"{output}.envi")
    return output


# ------------------------------------------------------------------- ENVI export
def export_envi(specpr_path: str, envi_path: str) -> str:
    """Export a convolved specpr library to ENVI spectral-library format.

    Writes a flat float32 BSQ binary plus a ``.hdr`` (wavelengths, record numbers,
    spectrum names) — the format emit-sds-l2b's Spectral-Library-Reader consumes.
    The grid records (wavelength/resolution/channel-number) lead, then the spectra.
    """
    records = load(specpr_path)

    # locate the wavelength grid for the header
    wl_rec = next(i for i, r in enumerate(records)
                  if _is_data_start(r) and _title(r).startswith("Wavelengths"))
    wavelengths = read_array(records, wl_rec)
    n_channels = wavelengths.size

    rows, names, rec_nums = [], [], []
    for i, r in enumerate(records):              # grid/setup rows first
        if _is_data_start(r):
            t = _title(r)
            if t.startswith("Wavelengths") or t.startswith("Resolution") or "Data value" in t:
                rows.append(read_array(records, i).astype(np.float32))
                names.append(t)
                rec_nums.append(i)
    for i in mineral_records(records):           # then the spectra
        rows.append(read_array(records, i).astype(np.float32))
        names.append(_title(records[i]))
        rec_nums.append(i)

    data = np.stack(rows)
    data[~np.isfinite(data)] = -1.23e34
    Path(envi_path).write_bytes(data.astype("<f4").tobytes())

    wl_str = ",".join(f"{w:.6g}" for w in wavelengths)
    rec_str = ",".join(str(r) for r in rec_nums)
    names_str = ", \n ".join(f"{n.replace(',', ';'):40s}" for n in names)
    hdr = (
        f"ENVI\n"
        f"file type = ENVI Spectral Library\n"
        f"bands = 1\n"
        f"samples = {n_channels}\n"
        f"lines = {len(rows)}\n"
        f"band names = {{Library translated from SPECPR}}\n"
        f"wavelength units  = Micrometers\n"
        f"wavelength = {{{wl_str}}}\n"
        f"record = {{{rec_str}}}\n"
        f"spectra names = {{ \n {names_str}}}\n"
        f"header offset = 0 \n"
        f"data type = 4\n"
        f"interleave = bsq \n"
        f"byte order = 0\n"
    )
    Path(envi_path + ".hdr").write_text(hdr)
    print(f"Wrote ENVI spectral library ({len(rows)} spectra, {n_channels} ch) -> {envi_path}")
    return envi_path
