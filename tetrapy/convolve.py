"""Build a convolved spectral library for an instrument — pure Python.

Tetracorder matches observed reflectance against a spectral library that has been
*convolved* to the instrument's channels. When a new calibration epoch appears
(new wavelengths/FWHM) the convolved library must be regenerated.

This is a from-scratch Python reimplementation of the USGS specpr convolution
(no Fortran/specpr needed). It:

  1. reads the unconvolved master library ``splib06b`` (specpr binary format),
  2. Gaussian-convolves each spectrum from its own native grid+resolution onto the
     target instrument grid (with a native-FWHM quadrature correction), and
  3. writes the result as a valid specpr library by overwriting the data in an
     existing convolved library used as a *template* — preserving record numbering,
     titles, text/description records and pointers exactly (Phil's "conserve
     indexing").

Validated against the shipped ``s06emitc``: median per-spectrum RMS ~4.4e-5
(reflectance 0-1), identical file structure. See docs/convolved-library-build.md.

specpr format (1536-byte big-endian records) is documented in
``specpr/specpr-format-2,3/specpr-format-v2.txt``; struct offsets cross-checked
against the opalpy reader and the C++ Spectral-Library-Reader.
"""

import re
import struct
from pathlib import Path

import numpy as np

RECLEN = 1536
HEAD_FMT = ">i40s8s16i60s74s74s74s74s6i260f"   # case 0: data-start (256 chan) — 1536 bytes
CONT_FMT = ">i383f"                            # case 1: data-continuation (383 chan)
IGNORE = -1.23e34                              # specpr bad/deleted-channel sentinel
FWHM_TO_SIGMA = 1.0 / 2.3548200450309493

# header field byte offsets we touch directly
OFF_ITCHAN = 80
OFF_DATA = 512


# --------------------------------------------------------------------------- I/O
def load(path):
    """Read a specpr file into a list of 1536-byte records."""
    d = Path(path).read_bytes()
    return [d[i * RECLEN:(i + 1) * RECLEN] for i in range(len(d) // RECLEN)]


def save(path, records):
    Path(path).write_bytes(b"".join(records))


def _is_data_start(rec):
    return (struct.unpack(">i", rec[:4])[0] & 3) == 0


def _title(rec):
    return struct.unpack(HEAD_FMT, rec)[1].decode("ascii", "ignore").rstrip("\x00 ")


def _itchan(rec):
    return struct.unpack(">i", rec[OFF_ITCHAN:OFF_ITCHAN + 4])[0]


def read_array(records, recno):
    """Reassemble the full float channel array of the spectrum at ``recno``.

    Bad/deleted channels (|v| >= 1e30) become NaN.
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


def write_array(records, recno, values):
    """Overwrite the float data at ``recno`` (head + continuations) in place.

    Header fields (title, itchan, irwav, irespt, pointers) are preserved. NaNs are
    written as the specpr IGNORE sentinel. ``records`` must be a mutable list.
    """
    n = _itchan(records[recno])
    v = np.asarray(values, dtype=np.float64).copy()
    v[~np.isfinite(v)] = IGNORE
    v = v.astype(">f4")
    if v.size != n:
        raise ValueError(f"record {recno}: got {v.size} values, itchan={n}")
    hb = bytearray(records[recno])
    k = min(256, n)
    hb[OFF_DATA:OFF_DATA + k * 4] = v[:k].tobytes()
    records[recno] = bytes(hb)
    off, c = k, recno + 1
    while off < n:
        cb = bytearray(records[c])
        take = min(383, n - off)
        cb[4:4 + take * 4] = v[off:off + take].tobytes()
        records[c] = bytes(cb)
        off += take
        c += 1


# ------------------------------------------------------------------- library map
def _is_setup(title):
    """True for non-mineral records (wavelength/resolution/reference/header/text)."""
    return (title.startswith("Wavelengths") or "Bandpass" in title or "FWHM" in title
            or "Resolution" in title or "Data value" in title
            or "Digital Spectral Library" in title or title.startswith("*")
            or title in ("", ".."))


def mineral_records(records):
    """Ordered list of record numbers holding mineral spectra (not setup records)."""
    return [i for i, r in enumerate(records)
            if _is_data_start(r) and not _is_setup(_title(r))]


def _wavelength_resolution_records(records):
    """Return {wavelength_recno: resolution_recno} pairing (wavelength -> next bandpass)."""
    waves, bands = [], []
    for i, r in enumerate(records):
        if _is_data_start(r):
            t = _title(r)
            if t.startswith("Wavelengths"):
                waves.append(i)
            elif "Bandpass" in t or "FWHM" in t:
                bands.append(i)
    return {w: min([b for b in bands if b > w], default=None) for w in waves}


def find_grid(records):
    """Return (wavelength_recno, resolution_recno) for a convolved library template."""
    wl = res = None
    for i, r in enumerate(records):
        if _is_data_start(r):
            t = _title(r)
            if wl is None and t.startswith("Wavelengths"):
                wl = i
            elif res is None and t.startswith("Resolution"):
                res = i
    if wl is None or res is None:
        raise ValueError("template has no Wavelengths/Resolution grid records")
    return wl, res


# --------------------------------------------------------------------- convolve
def convolve_spectrum(native_wl, native_val, native_fwhm, out_wl, out_fwhm):
    """Convolve one native spectrum onto the target grid.

    Gaussian-weighted resample with a native-FWHM quadrature correction
    (effective kernel FWHM = sqrt(out_fwhm^2 - native_fwhm^2)). Channels outside
    the native wavelength coverage are returned as NaN.
    """
    out = np.full(out_wl.shape, np.nan)
    m = np.isfinite(native_val) & np.isfinite(native_wl)
    nw, nv = native_wl[m], native_val[m]
    nf = native_fwhm[m] if native_fwhm is not None else None
    if nw.size < 2:
        return out
    lo, hi = nw.min(), nw.max()
    for i, (lam, tf) in enumerate(zip(out_wl, out_fwhm)):
        if lam < lo or lam > hi:
            continue
        if nf is not None:
            eff2 = tf * tf - nf[np.argmin(np.abs(nw - lam))] ** 2
            eff = np.sqrt(eff2) if eff2 > 1e-12 else tf * 0.05
        else:
            eff = tf
        s = eff * FWHM_TO_SIGMA
        w = np.exp(-0.5 * ((nw - lam) / s) ** 2)
        sel = w > 1e-6
        if sel.sum() < 2:
            continue
        out[i] = np.sum(w[sel] * nv[sel]) / np.sum(w[sel])
    return out


# ------------------------------------------------------------- ENVI target grid
def read_wavelengths_fwhm(hdr_path):
    """Read (wavelengths, fwhm) in microns from an EMIT ENVI header.

    Reflectance headers are ideal — the values self-consistently match the scene.
    EMIT headers are in nanometers; converted to microns here.
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


# ------------------------------------------------------------------ entry point
def build_convolved_library(master, template, output, envi_header=None, **_):
    """Regenerate a convolved spectral library in pure Python.

    \b
    Parameters
    ----------
    master : str
        Unconvolved master library (specpr format, e.g. splib06b).
    template : str
        An existing convolved library (same instrument/channel count) whose record
        structure, titles, and text/description records are reused verbatim; only
        the numeric data is overwritten. Preserves record indexing.
    output : str
        Output convolved-library path.
    envi_header : str | None
        EMIT reflectance ENVI header to take the target wavelength/FWHM grid from.
        If omitted, the template's own grid is reused (re-convolve, same grid).
    """
    mrecs = load(master)
    trecs = load(template)

    wl_rec, res_rec = find_grid(trecs)
    if envi_header:
        out_wl, out_fwhm = read_wavelengths_fwhm(envi_header)
        n = _itchan(trecs[wl_rec])
        if out_wl.size != n:
            raise ValueError(f"ENVI grid has {out_wl.size} channels, template expects {n}")
        write_array(trecs, wl_rec, out_wl)
        write_array(trecs, res_rec, out_fwhm)
    else:
        out_wl = read_array(trecs, wl_rec)
        out_fwhm = read_array(trecs, res_rec)

    m_min = mineral_records(mrecs)
    t_min = mineral_records(trecs)
    if len(m_min) != len(t_min):
        raise ValueError(f"master has {len(m_min)} spectra, template {len(t_min)} — "
                         "cannot align by order")

    pairs = _wavelength_resolution_records(mrecs)
    for k, (si, ti) in enumerate(zip(m_min, t_min)):
        irwav = struct.unpack(HEAD_FMT, mrecs[si])[15]
        nwl = read_array(mrecs, irwav)
        nval = read_array(mrecs, si)
        bp = pairs.get(irwav)
        nfwhm = read_array(mrecs, bp) if bp else None
        conv = convolve_spectrum(nwl, nval, nfwhm, out_wl, out_fwhm)
        write_array(trecs, ti, conv)

    save(output, trecs)
    print(f"Wrote {len(t_min)} convolved spectra ({out_wl.size} ch) -> {output}")
    return output


def compare_libraries(a, b, **_):
    """Report per-spectrum RMS between two convolved libraries (aligned by order)."""
    ra, rb = load(a), load(b)
    ma, mb = mineral_records(ra), mineral_records(rb)
    if len(ma) != len(mb):
        raise ValueError(f"{len(ma)} vs {len(mb)} spectra")
    diffs = []
    for ia, ib in zip(ma, mb):
        x, y = read_array(ra, ia), read_array(rb, ib)
        g = np.isfinite(x) & np.isfinite(y)
        if g.sum() >= 10:
            diffs.append(float(np.sqrt(np.mean((x[g] - y[g]) ** 2))))
    d = np.array(diffs)
    print(f"{len(d)} spectra: median RMS={np.median(d):.6g} mean={d.mean():.6g} "
          f"p95={np.percentile(d, 95):.6g} max={d.max():.6g}")
    return {"n": len(d), "median": float(np.median(d)), "max": float(d.max())}
