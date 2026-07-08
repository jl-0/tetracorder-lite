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
from typing import Dict, List, Optional, Tuple

import numpy as np
from numpy.typing import NDArray

RECLEN = 1536
HEAD_FMT = ">i40s8s16i60s74s74s74s74s6i260f"   # case 0: data-start (256 chan) — 1536 bytes
CONT_FMT = ">i383f"                            # case 1: data-continuation (383 chan)
IGNORE = -1.23e34                              # specpr bad/deleted-channel sentinel
FWHM_TO_SIGMA = 1.0 / 2.3548200450309493

# header field byte offsets we touch directly
OFF_ITCHAN = 80
OFF_DATA = 512


# --------------------------------------------------------------------------- I/O
def load(path: str) -> List[bytes]:
    """
    Read a specpr file into a list of 1536-byte records.

    Parameters
    ----------
    path : str
        Path to the specpr binary file.

    Returns
    -------
    List[bytes]
        List of fixed-length (1536-byte) records from the specpr file.
    """
    d = Path(path).read_bytes()
    return [d[i * RECLEN:(i + 1) * RECLEN] for i in range(len(d) // RECLEN)]


def save(path: str, records: List[bytes]) -> None:
    """
    Write a list of records to a specpr file.

    Parameters
    ----------
    path : str
        Output path for the specpr binary file.
    records : List[bytes]
        List of 1536-byte records to write.
    """
    Path(path).write_bytes(b"".join(records))


def _is_data_start(rec: bytes) -> bool:
    """
    Check if a record is a data-start record (case 0).

    Parameters
    ----------
    rec : bytes
        1536-byte specpr record.

    Returns
    -------
    bool
        True if the record is a data-start record.
    """
    return (struct.unpack(">i", rec[:4])[0] & 3) == 0


def _title(rec: bytes) -> str:
    """
    Extract the title string from a specpr record.

    Parameters
    ----------
    rec : bytes
        1536-byte specpr record.

    Returns
    -------
    str
        ASCII title string with null bytes and trailing spaces removed.
    """
    return struct.unpack(HEAD_FMT, rec)[1].decode("ascii", "ignore").rstrip("\x00 ")


def _itchan(rec: bytes) -> int:
    """
    Extract the channel count (itchan field) from a specpr record.

    Parameters
    ----------
    rec : bytes
        1536-byte specpr record.

    Returns
    -------
    int
        Number of channels in the spectrum.
    """
    return struct.unpack(">i", rec[OFF_ITCHAN:OFF_ITCHAN + 4])[0]


def read_array(records: List[bytes], recno: int) -> NDArray[np.float64]:
    """
    Reassemble the full float channel array of the spectrum at a given record number.

    This function reads a spectrum's data from the specpr file, spanning multiple
    records if necessary (data-start record plus continuation records). Invalid
    channels are converted to NaN.

    Parameters
    ----------
    records : List[bytes]
        List of all records from the specpr file.
    recno : int
        Record number (0-indexed) of the spectrum's data-start record.

    Returns
    -------
    NDArray[np.float64]
        Array of channel values. Bad/deleted channels (|v| >= 1e30) are set to NaN.
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


def write_array(records: List[bytes], recno: int, values: NDArray[np.float64]) -> None:
    """
    Overwrite the float data at a given record number (head + continuations) in place.

    This function updates the numeric data portion of a spectrum while preserving
    all header metadata. The records list is modified in place.

    Parameters
    ----------
    records : List[bytes]
        List of all records from the specpr file. Must be mutable.
    recno : int
        Record number (0-indexed) of the spectrum's data-start record.
    values : NDArray[np.float64]
        New channel values to write. Length must match the record's itchan field.

    Raises
    ------
    ValueError
        If the number of values doesn't match the record's itchan field.

    Notes
    -----
    Header fields (title, itchan, irwav, irespt, pointers) are preserved.
    NaN values are written as the specpr IGNORE sentinel (-1.23e34).
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
def _is_setup(title: str) -> bool:
    """
    Check if a record title indicates a non-mineral setup record.

    Parameters
    ----------
    title : str
        Record title string from the specpr header.

    Returns
    -------
    bool
        True for non-mineral records (wavelength/resolution/reference/header/text).
    """
    return (title.startswith("Wavelengths") or "Bandpass" in title or "FWHM" in title
            or "Resolution" in title or "Data value" in title
            or "Digital Spectral Library" in title or title.startswith("*")
            or title in ("", ".."))


def mineral_records(records: List[bytes]) -> List[int]:
    """
    Get ordered list of record numbers holding mineral spectra.

    Parameters
    ----------
    records : List[bytes]
        List of all records from the specpr file.

    Returns
    -------
    List[int]
        Record numbers (0-indexed) of mineral spectra, excluding setup records.
    """
    return [i for i, r in enumerate(records)
            if _is_data_start(r) and not _is_setup(_title(r))]


def _wavelength_resolution_records(records: List[bytes]) -> Dict[int, Optional[int]]:
    """
    Return wavelength-to-resolution record pairing.

    For each wavelength record, finds the next bandpass/FWHM record that follows it.

    Parameters
    ----------
    records : List[bytes]
        List of all records from the specpr file.

    Returns
    -------
    Dict[int, Optional[int]]
        Mapping from wavelength record number to its corresponding resolution
        record number. Value is None if no resolution record follows.
    """
    waves, bands = [], []
    for i, r in enumerate(records):
        if _is_data_start(r):
            t = _title(r)
            if t.startswith("Wavelengths"):
                waves.append(i)
            elif "Bandpass" in t or "FWHM" in t:
                bands.append(i)
    return {w: min([b for b in bands if b > w], default=None) for w in waves}


def find_grid(records: List[bytes]) -> Tuple[int, int]:
    """
    Find the wavelength and resolution grid records in a convolved library template.

    Parameters
    ----------
    records : List[bytes]
        List of all records from the specpr file.

    Returns
    -------
    Tuple[int, int]
        Tuple of (wavelength_recno, resolution_recno) for the target instrument grid.

    Raises
    ------
    ValueError
        If the template is missing Wavelengths or Resolution grid records.
    """
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
def convolve_spectrum(native_wl, native_val, native_fwhm, out_wl, out_fwhm,
                      tlim=1e-7):
    """Convolve one native spectrum onto the target grid.

    Matches the Fortran specpr convolution (gfiles + ggauss + convol + delx):
      - Quadrature subtraction: effective FWHM = sqrt(out² - native²)
      - When out_fwhm <= native_fwhm: fallback to native_fwhm * 0.01, snap center
        to nearest native channel (gfiles.r lines 284-290)
      - Channels outside native wavelength range marked as deleted (gfiles.r line 300)
      - Trapezoidal channel-spacing weighting (delx)
      - Gaussian weight threshold ``tlim`` (default 1e-7, Fortran default)
      - Normalized convolution (nmode=1 in convol.r)
    """
    n_out = out_wl.size
    n_in = native_wl.size
    out = np.full(n_out, np.nan)

    good = (np.abs(native_val) < 1e30) & (np.abs(native_wl) < 1e30)
    if good.sum() < 2:
        return out

    good_idx = np.where(good)[0]
    gw = native_wl[good_idx]
    gv = native_val[good_idx]

    # Fortran gfiles.r computes wavmin/wavmax from the WAVELENGTH array (all channels),
    # not just channels with good data. This allows Gaussian tails to reach data
    # channels even when the output center is in a "bad data" region.
    wl_valid = native_wl[np.abs(native_wl) < 1e30]
    lo, hi = wl_valid.min(), wl_valid.max()

    # Pre-compute channel spacing (delx equivalent)
    n_good = len(good_idx)
    dx = np.zeros(n_good)
    for ki in range(n_good):
        if ki == 0:
            dx[ki] = abs(gw[1] - gw[0])
        elif ki == n_good - 1:
            dx[ki] = abs(gw[-1] - gw[-2])
        else:
            dx[ki] = abs(gw[ki + 1] - gw[ki - 1]) * 0.5

    # Resolve native FWHM for lookup
    nf = native_fwhm[good_idx] if native_fwhm is not None else None

    for i in range(n_out):
        lam = out_wl[i]
        bw = out_fwhm[i]
        if bw <= 0 or not np.isfinite(bw):
            continue

        # gfiles.r: channels outside native range are deleted
        if lam < lo or lam > hi:
            continue

        # gfiles.r: quadrature subtraction of native resolution
        if nf is not None:
            conind = np.argmin(np.abs(gw - lam))
            native_res = nf[conind]
            if bw <= native_res:
                # Fortran fallback: bwidth = native_res * 0.01, snap center
                eff_bw = native_res * 0.01
                lam = gw[conind]
            else:
                eff_bw = np.sqrt(bw * bw - native_res * native_res)
        else:
            eff_bw = bw

        # ggauss: ax = -4*ln2 / FWHM^2
        ax = -2.772589 / (eff_bw * eff_bw)

        # convol: trapezoidal sum with Gaussian weights
        diff = gw - lam
        gauss = np.exp(ax * diff * diff)
        sel = gauss > tlim
        if sel.sum() < 1:
            continue

        xx = gauss[sel] * dx[sel]
        nrmsum = np.sum(xx)
        if nrmsum > 0.0:
            out[i] = np.sum(gv[sel] * xx) / nrmsum

    return out


# ------------------------------------------------------------- ENVI target grid
def read_wavelengths_fwhm(hdr_path: str) -> Tuple[NDArray[np.float64], NDArray[np.float64]]:
    """
    Read wavelengths and FWHM in microns from an EMIT ENVI header.

    This function extracts the wavelength and FWHM arrays from an ENVI header file
    and converts them to micrometers if necessary. EMIT headers typically use
    nanometers, which are automatically converted.

    Parameters
    ----------
    hdr_path : str
        Path to the ENVI header file (.hdr). If the path doesn't end in .hdr,
        the function attempts to find the corresponding header file.

    Returns
    -------
    Tuple[NDArray[np.float64], NDArray[np.float64]]
        Tuple of (wavelengths, fwhm) arrays in micrometers.

    Raises
    ------
    ValueError
        If the header is missing 'wavelength' or 'fwhm' fields, or if the
        wavelength units are unexpected.

    Notes
    -----
    Reflectance headers are ideal as the values self-consistently match the scene.
    Supported wavelength units: nanometers (nm), micrometers (um, μm, microns).
    EMIT headers are in nanometers and are automatically converted to microns.
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


# --------------------------------------------------------------- recipe parsing
# A convolution "recipe" enumerates, per output spectrum, the master records to
# read: the spectrum (recnum), its native wavelength grid (inwave) and native
# resolution/FWHM (inres), plus the output title. This is exactly the information
# the USGS Fortran read from a ``conv.*.cmds`` command script; a ``.csv`` with
# columns ``inwave,inres,recnum,title`` is an equivalent, GUI-free encoding.
RECIPE_FIELDS = ("inwave", "inres", "recnum", "title")


def parse_cmds(path):
    """Parse a specpr ``conv.*.cmds`` convolution script into recipe rows.

    Each ``######## convolve spectrum`` block defines one output spectrum via
    ``==[inwave]``, ``==[inres]``, ``==[Recnum]`` (master record numbers; the
    leading specpr device letter is ignored) and ``==[Title]``. All other lines
    (GUI keystrokes, padding commands, display toggles) have no effect on the
    convolved output and are ignored. Returns a list of dicts with keys
    ``inwave, inres, recnum, title``.
    """
    rows, cur = [], None
    for line in Path(path).read_text(errors="ignore").splitlines():
        if "convolve spectrum" in line:
            if cur is not None and "recnum" in cur:
                rows.append(cur)
            cur = {}
            continue
        if cur is None:
            continue
        m = re.match(r"==\[(inwave|inres|Recnum)\]\s*[A-Za-z]?(\d+)", line)
        if m:
            cur[m.group(1).lower()] = int(m.group(2))
            continue
        mt = re.match(r"==\[Title\](.*)", line)
        if mt:
            cur["title"] = mt.group(1).rstrip()
    if cur is not None and "recnum" in cur:
        rows.append(cur)
    return rows


def parse_csv(path):
    """Parse a recipe CSV (columns ``inwave,inres,recnum[,title]``).

    ``title`` is optional; when absent/blank the master record's own title is used
    at build time. This is the documented, GUI-free recipe format that supersedes
    the ``.cmds`` script for new libraries.
    """
    import csv
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            row = {"inwave": int(r["inwave"]), "inres": int(r["inres"]),
                   "recnum": int(r["recnum"])}
            title = (r.get("title") or "").strip()
            if title:
                row["title"] = title
            rows.append(row)
    return rows


def read_recipe(path):
    """Load a convolution recipe from a ``.csv`` or a specpr ``.cmds`` file."""
    return parse_csv(path) if str(path).lower().endswith(".csv") else parse_cmds(path)


def cmds_to_csv(cmds_path, csv_path):
    """Convert a specpr ``conv.*.cmds`` script to the documented recipe CSV.

    One-shot migration/inspection helper — not part of a normal build.
    """
    import csv
    rows = parse_cmds(cmds_path)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RECIPE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in RECIPE_FIELDS})
    print(f"Wrote {len(rows)} recipe rows -> {csv_path}")
    return csv_path


# ---------------------------------------------------------- specpr record build
IGNORE_F4 = np.float32(IGNORE)


def _text_record(title, icflag=2):
    """Build a specpr text record (icflag&3==2) with ``title`` in the header.

    Used for the library-title banner and the ``..`` spacer/padding records that
    preserve record numbering. Trailing filler bytes are inessential (specpr reads
    only icflag + title), so we leave them zeroed.
    """
    rec = bytearray(RECLEN)
    struct.pack_into(">i", rec, 0, icflag)
    rec[4:44] = title.encode("ascii", "replace")[:40].ljust(40, b" ")
    struct.pack_into(">i", rec, 56, 41)  # itxtch — cosmetic char count
    return bytes(rec)


def _data_block(shell, title, values):
    """Build a data-start record (+ continuations) from a cloned record ``shell``.

    ``shell`` is a real master data-start record (valid specpr header: dates,
    flags, pointers). We overwrite title, ``itchan`` and the float data with
    ``values``; NaNs become the specpr IGNORE sentinel. Returns a list of records
    (head + as many continuation records as needed for ``len(values)``).
    """
    n = len(values)
    v = np.asarray(values, dtype=np.float64).copy()
    v[~np.isfinite(v)] = IGNORE
    v = v.astype(">f4")

    head = bytearray(shell)
    head[4:44] = title.encode("ascii", "replace")[:40].ljust(40, b" ")
    struct.pack_into(">i", head, OFF_ITCHAN, n)         # itchan
    k = min(256, n)
    head[OFF_DATA:OFF_DATA + k * 4] = v[:k].tobytes()
    # zero any stale data beyond the written channels in the header record
    if k < 256:
        head[OFF_DATA + k * 4:OFF_DATA + 256 * 4] = b"\x00" * ((256 - k) * 4)
    out = [bytes(head)]

    off = k
    while off < n:
        cont = bytearray(RECLEN)
        struct.pack_into(">i", cont, 0, 1)              # icflag&3==1 continuation
        take = min(383, n - off)
        cont[4:4 + take * 4] = v[off:off + take].tobytes()
        out.append(bytes(cont))
        off += take
    return out


# ------------------------------------------------------------------ entry point
def build_all(spectral_lib_dir, recipe_dir, output_dir, envi_header):
    """Convolve every library whose recipe + master are present.

    Recipes are discovered by prefix in ``recipe_dir``: ``conv.s06*.cmds`` (or
    ``.csv``) drives the standard library from ``splib06b``; ``conv.r06*.cmds``
    drives the research library from ``sprlb06b``. A family is skipped (with a log
    line, not an error) when its recipe or master is absent, so a standard-only
    deployment still works. The output grid always comes from ``envi_header`` (the
    L2A reflectance header).

    \b
    Parameters
    ----------
    spectral_lib_dir : str
        Directory holding the unconvolved master libraries (splib06b / sprlb06b).
    recipe_dir : str
        Directory holding ``conv.s06*`` / ``conv.r06*`` recipe files (.cmds/.csv).
    output_dir : str
        Directory to write ``s06emit_convolved`` / ``r06emit_convolved`` (+ ENVI).
    envi_header : str
        EMIT L2A reflectance ENVI header supplying the target wavelength/FWHM grid.
    """
    families = [
        {"glob": "conv.s06*", "master": "splib06b", "output": "s06emit_convolved"},
        {"glob": "conv.r06*", "master": "sprlb06b", "output": "r06emit_convolved"},
    ]
    outputs = []
    for fam in families:
        recipes = sorted(p for p in Path(recipe_dir).glob(fam["glob"])
                         if p.suffix in (".cmds", ".csv"))
        master = Path(spectral_lib_dir) / fam["master"]
        if not recipes:
            print(f"skip {fam['output']}: no recipe {fam['glob']}[.cmds|.csv] in {recipe_dir}")
            continue
        if not master.exists():
            print(f"skip {fam['output']}: master not found ({master})")
            continue
        if len(recipes) > 1:
            print(f"note: multiple {fam['glob']} recipes, using {recipes[0].name}")
        output = f"{output_dir}/{fam['output']}"
        print(f"=== convolving {master.name} via {recipes[0].name} -> {output} ===")
        build_from_recipe(master=str(master), recipe=str(recipes[0]),
                          output=output, envi_header=envi_header)
        export_envi(output, f"{output}_envi")
        outputs.append(output)
    if not outputs:
        raise RuntimeError("no library families could be built (no recipe+master found)")
    return outputs


def build_from_recipe(master, recipe, output, envi_header, sppad=4):
    """Build a convolved spectral library from a master + recipe, no template.

    Reproduces the specpr record layout the Fortran ``conv.*.cmds`` produced — a
    30-record header (library banner, then Wavelengths / Resolution / channel-number
    grid records) followed by one fixed-size block per spectrum (data-start +
    continuation + ``sppad`` padding records) — so absolute record numbers match
    what Tetracorder's fit-scripts expect.

    \b
    Parameters
    ----------
    master : str
        Unconvolved master library (specpr format, e.g. splib06b).
    recipe : str
        Convolution recipe (``conv.*.cmds`` or ``.csv``) selecting master records
        and native grids per spectrum.
    output : str
        Output convolved-library path.
    envi_header : str
        EMIT L2A reflectance ENVI header supplying the target wavelength/FWHM grid.
    sppad : int
        Padding text records per spectrum (Fortran used 4); sets the record stride.
    """
    mrecs = load(master)
    rows = read_recipe(recipe)
    out_wl, out_fwhm = read_wavelengths_fwhm(envi_header)
    n_ch = out_wl.size
    channel_axis = np.arange(1, n_ch + 1, dtype=np.float64)

    # A valid data-start header shell to clone (dates/flags/pointers preserved).
    shell = mrecs[rows[0]["recnum"]] if rows else None
    if shell is None:
        raise ValueError(f"recipe {recipe} has no spectra")

    # ---- header preamble (records 0-29), matching shipped s06emitc spacing ----
    name = Path(output).name
    records = [
        _text_record(f"USGS Digital Spectral Library: {name}"),
        _text_record(f"USGS Digital Spectral Library: {name}"),
        _text_record(f"Convolved library {n_ch} ch (built by tetrapy)"),
        _text_record("*" * 40),
        _text_record("*" * 40),
        _text_record(".."),
    ]
    # PDS-style label expected at record 0
    pds = bytearray(RECLEN)
    pds[:52] = b"SPECPR_FS=2.0\r\nRECORD_BYTES=1536\r\nLABEL_RECORDS=1\r\n\x00\x00"[:52]
    records[0] = bytes(pds)

    def append_grid(title, values, block_end):
        """Append a grid data-block then pad with '..' up to record block_end."""
        for r in _data_block(shell, title, values):
            records.append(r)
        while len(records) < block_end:
            records.append(_text_record(".."))

    append_grid(f"Wavelengths in microns {n_ch} ch EMIT", out_wl, 12)
    append_grid(f"Resolution  in microns {n_ch} ch EMIT", out_fwhm, 18)
    append_grid(f"Data value = channel number ({n_ch} ch)", channel_axis, 30)
    assert len(records) == 30, f"header preamble is {len(records)} records, expected 30"

    # ---- one fixed-size block per recipe row ----
    # Each row consumes the same number of records whether or not its spectrum can
    # be convolved, so absolute record numbers stay aligned with what Tetracorder's
    # fit-scripts expect. A row whose master recnum is missing/invalid (e.g. a
    # recipe of a newer vintage than the master present) is written as a
    # placeholder block of all-deleted channels, NOT dropped — dropping would shift
    # every subsequent record number.
    block = 2 + sppad  # data-start + 1 continuation (n_ch>256) + sppad padding
    n_master = len(mrecs)
    built, placeholders = 0, []
    nan_ch = np.full(n_ch, np.nan)
    for row in rows:
        rn, iw, ir = row["recnum"], row["inwave"], row["inres"]
        valid = 0 <= rn < n_master and _is_data_start(mrecs[rn])
        if valid:
            conv = convolve_spectrum(read_array(mrecs, iw), read_array(mrecs, rn),
                                     read_array(mrecs, ir), out_wl, out_fwhm)
            title = row.get("title") or _title(mrecs[rn])
            built += 1
        else:
            conv = nan_ch                              # deleted-data placeholder
            title = row.get("title") or f"MISSING recnum {rn}"
            placeholders.append(title)

        blk = _data_block(shell, title, conv)          # head + 1 continuation
        blk += [_text_record("..") for _ in range(block - len(blk))]
        records.extend(blk)

    save(output, records)
    print(f"Wrote {built} convolved spectra ({n_ch} ch) -> {output}")
    if placeholders:
        print(f"note: {len(placeholders)} recipe rows had no master spectrum — "
              f"written as deleted-data placeholders to preserve record numbering")
        for t in placeholders[:10]:
            print(f"      placeholder: {t}")
    return output


def export_envi(specpr_path: str, envi_path: str) -> str:
    """
    Export a specpr convolved library to ENVI spectral library format.

    This function converts a specpr-format spectral library to ENVI format,
    which is required by some processing pipelines. The output includes both
    a binary data file and a .hdr metadata file.

    Parameters
    ----------
    specpr_path : str
        Path to the input specpr convolved library.
    envi_path : str
        Output path for the ENVI library (without .hdr extension).

    Returns
    -------
    str
        Path to the output ENVI library.

    Notes
    -----
    Writes a flat float32 binary (BSQ, little-endian) plus a .hdr with wavelengths,
    record numbers, and spectra names — matching the format used by emit-sds-l2b's
    Spectral-Library-Reader.

    The output includes setup records (wavelength, resolution, channel-number) as
    the first rows, followed by all mineral spectra. Invalid values (NaN) are
    written as -1.23e34 (specpr IGNORE sentinel).
    """
    records = load(specpr_path)
    wl_rec, _ = find_grid(records)
    wavelengths = read_array(records, wl_rec)
    n_channels = wavelengths.size

    mins = mineral_records(records)
    spectra = []
    names = []
    rec_nums = []
    for i in mins:
        spectra.append(read_array(records, i).astype(np.float32))
        names.append(_title(records[i]))
        rec_nums.append(i)

    # Include the setup records (wavelength, resolution, channel-number) as first rows
    setup_recs = []
    for i, r in enumerate(records):
        if _is_data_start(r):
            t = _title(r)
            if t.startswith("Wavelengths") or t.startswith("Resolution") or "Data value" in t:
                setup_recs.append((i, t, read_array(records, i).astype(np.float32)))

    all_rows = []
    all_names = []
    all_rec_nums = []
    for ri, name, arr in setup_recs:
        all_rows.append(arr)
        all_names.append(name)
        all_rec_nums.append(ri)
    for arr, name, ri in zip(spectra, names, rec_nums):
        all_rows.append(arr)
        all_names.append(name)
        all_rec_nums.append(ri)

    data = np.stack(all_rows)
    data[~np.isfinite(data)] = -1.23e34

    out = Path(envi_path)
    out.write_bytes(data.astype("<f4").tobytes())

    wl_str = ",".join(f"{w:.6g}" for w in wavelengths)
    rec_str = ",".join(str(r) for r in all_rec_nums)
    names_str = ", \n ".join(f"{n:40s}" for n in all_names)

    hdr = (
        f"ENVI\n"
        f"file type = ENVI Spectral Library\n"
        f"bands = 1\n"
        f"samples = {n_channels}\n"
        f"lines = {len(all_rows)}\n"
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
    print(f"Wrote ENVI spectral library ({len(all_rows)} spectra, {n_channels} ch) -> {envi_path}")
    return envi_path


def compare_libraries(a: str, b: str, **_) -> Dict[str, float]:
    """
    Report per-spectrum RMS differences between two convolved libraries.

    This function validates a newly generated convolved library against a reference
    by computing RMS (root mean square) differences for each aligned spectrum pair.

    Parameters
    ----------
    a : str
        Path to the first convolved library (specpr format).
    b : str
        Path to the second convolved library (specpr format).

    Returns
    -------
    Dict[str, float]
        Dictionary with keys:
        - 'n': Number of spectra compared
        - 'median': Median RMS difference
        - 'max': Maximum RMS difference

    Raises
    ------
    ValueError
        If the two libraries have different numbers of mineral spectra.

    Notes
    -----
    Libraries are aligned by order (i-th spectrum in library A is compared to
    i-th spectrum in library B). Only finite values present in both spectra
    are included in the RMS calculation. Channels with NaN in either spectrum
    are excluded.

    RMS differences around 4-5e-5 (reflectance 0-1 scale) indicate excellent
    agreement, as validated against the shipped USGS libraries.
    """
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
