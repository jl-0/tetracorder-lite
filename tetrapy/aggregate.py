import logging
from pathlib import Path

import numpy as np
import spectral.io.envi as envi
import xarray as xr
from scipy.interpolate import interp1d

from tetrapy.tetracorder import TetraDecoder

import warnings
from rasterio.errors import NotGeoreferencedWarning

# Very spammy, just turn them off
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)


Logger = logging.getLogger(__name__)

# ENVI "data type" -> numpy dtype (only the types the convolved libraries use)
DTYPES = {1: "u1", 2: "i2", 4: "f4", 5: "f8", 12: "u2"}


def read_library(path: str) -> dict[str, list[int] | np.ndarray]:
    """
    Read an ENVI spectral library into records and reflectance.

    Reads the raw binary directly (rather than via ``spectral``'s ``SpectralLibrary``,
    which rejects headers whose ``spectra names`` count disagrees with ``lines``).

    Parameters
    ----------
    path : str
        Path to the ENVI library binary (its ``.hdr`` sits alongside).

    Returns
    -------
    dict
        ``{"records": list[int], "rfl": ndarray}`` where ``rfl`` is the reflectance
        array shaped ``(n_records, n_bands)`` and ``records`` are the library record
        numbers in row order.
    """
    hdr = envi.read_envi_header(path + ".hdr")

    lines = int(hdr["lines"])
    samples = int(hdr["samples"])

    dtype = DTYPES[int(hdr["data type"])]
    order = ">" if int(hdr.get("byte order", 0)) == 1 else "<"

    data = np.fromfile(path, dtype=np.dtype(f"{order}{dtype}"))
    data = data.reshape(lines, samples).astype(np.float32)

    return {
        "records": [int(r) for r in hdr["record"]],
        "rfl": data,
    }


def aggregate(
    decoder: TetraDecoder,
    group: int,
    rfl: xr.DataArray | None = None,
    uncert: xr.DataArray | None = None,
    libs: dict | None = None,
) -> tuple[xr.DataArray | None, xr.DataArray | None]:
    """
    Aggregate one tetracorder group into band-depth / mineral-id maps.

    Walks every decoded block in ``group``, loads its ``.depth``/``.fit`` outputs,
    and composites them into a two-band ``(band, y, x)`` array: band 0 is the scaled
    band depth, band 1 is the (1-based) mineral index of the winning block per pixel.
    A matching uncertainty array carries the Clark-2003 band-depth uncertainty (band
    0) and the fit (band 1). Uncertainty is only computed when the reflectance inputs
    and libraries are supplied.

    Parameters
    ----------
    decoder : TetraDecoder
        Decoded expert system providing the blocks and their output paths.
    group : int
        Group number to aggregate (e.g. ``1`` or ``2``).
    rfl : xarray.DataArray, optional
        Observed reflectance as ``(y, x, band)`` carrying a ``wavelength`` coordinate,
        aligned with the depth/fit rasters. Required (with ``uncert`` and ``libs``)
        for uncertainty.
    uncert : xarray.DataArray, optional
        Observed reflectance uncertainty, same shape/ordering as ``rfl``.
    libs : dict, optional
        Library id -> ``{"records": [...], "rfl": ndarray}`` as produced by
        :func:`read_library`. Its presence is what enables the uncertainty band.

    Returns
    -------
    tuple[xarray.DataArray | None, xarray.DataArray | None]
        ``(abun, abununcert)``, each ``(band=2, y, x)``, or ``(None, None)`` if the
        group had no valid input.
    """
    Logger.debug(f"Aggregating group {group}")
    blocks = decoder.get_groups([group])

    # libs only exists if we're calculating uncertainty
    if libs:
        wl = rfl.wavelength.data
        if (wl > 100).any():
            wl = wl / 1000

    # Tracking statistics
    c = 0
    t = len(blocks)

    abun = None
    abununcert = None
    for i, block in enumerate(blocks, start=1):
        name = block["title"]
        base = decoder.root / block["path"]

        # Find the data files
        if not (depth := base.with_name(f"{base.name}.depth.gz")).exists():
            Logger.debug(f"[{i:03}/{t:03}] - Depth file not found for {name}")
            continue

        if not (fit := base.with_name(f"{base.name}.fit.gz")).exists():
            Logger.debug(f"[{i:03}/{t:03}] - Fit file not found for {name}")
            continue

        # Load the data in
        depth = xr.open_dataset(depth, engine="rasterio")["band_data"].squeeze()
        valid = depth > 0
        if not valid.any():
            Logger.debug(f"[{i:03}/{t:03}] - No valid data for {name}")
            continue

        # Copy shape from first valid input
        if abun is None:
            y = depth.y
            x = depth.x
            abun = xr.DataArray(
                np.zeros((2, y.size, x.size)),
                dims=("band", "y", "x"),
                coords={
                    "band": range(2),
                    "y": y,
                    "x": x,

                },
                name="band_data",
            )
            abununcert = xr.zeros_like(abun)

        fit = xr.open_dataset(fit, engine="rasterio")["band_data"].squeeze()

        # Apply scaling factor
        depth = depth / 255.0 * 0.5
        fit = fit / 255.0 * 0.5

        # Band Depth
        abun[0] = abun[0].where(~valid, depth)

        # Mineral ID
        abun[1] = abun[1].where(~valid, i)

        # Uncertainty
        if libs:
            lib = libs[block["library"]]
            rec = block["record"]
            if rec in lib["records"]:
                unc = calculate_uncertainty(
                    wl = wl,
                    rfl = rfl.data[valid.data],
                    uncert = uncert.data[valid.data],
                    lib = lib["rfl"][lib["records"].index(rec)],
                    features = block["features"],
                )

                full = xr.zeros_like(valid, dtype=float)
                full.data[valid] = unc

                abununcert[0] = abununcert[0].where(~valid, full)
            else:
                Logger.warning(f"[{i:03}/{t:03}] * Library record {rec} not found in {block['library']}, cannot calculate uncertainty for {name}")

        # Fit
        abununcert[1] = abununcert[1].where(~valid, fit)

        c += 1
        Logger.debug(f"[{i:03}/{t:03}] + Added {name}")

    Logger.debug(f"{c}/{t} ({c / t:.1%}) Blocks successfully aggregated")
    return abun, abununcert


def build(
    output: str | None,
    tetracorder: str,
    rfl: str | None = None,
    rfluncert: str | None = None,
    reflib: str = "/root/tetracorder/sl1/usgs/tetrapy/reflib.envi",
    reslib: str = "/root/tetracorder/sl1/usgs/tetrapy/reslib.envi",
) -> tuple[xr.DataArray, xr.DataArray]:
    """
    Aggregate groups 1 and 2 into the L2B mineral / uncertainty products.

    Parameters
    ----------
    output : str or None
        Directory to write ``abun.nc`` and ``abununcert.nc`` to. Pass ``None`` to
        return the products without writing them.
    tetracorder : str
        Path to the tetracorder output directory (holding the expert system file).
    rfl, rfluncert : str, optional
        Paths to the observed reflectance and reflectance-uncertainty rasters. Both
        must be given to enable band-depth uncertainty calculation.
    reflib, reslib : str, optional
        Paths to the convolved reference (``splib06``) and research (``sprlb06``)
        ENVI libraries. Only read when uncertainty is being calculated.

    Returns
    -------
    tuple[xarray.DataArray, xarray.DataArray]
        The 4-band abundance and uncertainty stacks (group 1 then group 2).
    """
    tc = TetraDecoder(tetracorder)

    libs = None
    if rfl and rfluncert:
        Logger.info("Loading reflectance products")

        # Transpose to stay consistent with the tetracorder products
        rfl = xr.open_dataset(rfl, engine="rasterio")["band_data"]
        rfl = rfl.transpose("y", "x", "band").load()

        rfluncert = xr.open_dataset(rfluncert, engine="rasterio")
        rfluncert = rfluncert["band_data"].transpose("y", "x", "band").load()

        libs = {
            "sprlb06": read_library(reslib),
            "splib06": read_library(reflib),
        }

    abun1, uncert1 = aggregate(tc, 1, rfl, rfluncert, libs)
    abun2, uncert2 = aggregate(tc, 2, rfl, rfluncert, libs)

    # Bands: Depth 1, Min ID 1, Depth 2, Min ID 2
    abun = xr.concat([abun1, abun2], "band")
    abun["band"] = range(1, 5)

    # Bands: Uncert 1, Fit 1, Uncert 2, Fit 2
    uncert = xr.concat([uncert1, uncert2], "band")
    uncert["band"] = range(1, 5)

    # Combine and export
    if output is not None:
        output = Path(output)
        Logger.info(f"Writing to {output}")
        abun.to_dataset().to_netcdf(output / "abun.nc")
        uncert.to_dataset().to_netcdf(output / "abununcert.nc")

    return abun, uncert


def cont_rem(
    wavelengths: np.ndarray,
    reflectance: np.ndarray,
    continuum_idx: tuple[np.ndarray, np.ndarray],
    valid_wavelengths: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Continuum-remove a reflectance spectrum (Kaufman and Tanre, 1992).

    Draws a straight line between the mean reflectance of the left and right
    continuum windows and returns ``1 - reflectance / continuum`` over the feature
    bands (those valid channels spanning the two windows).

    Parameters
    ----------
    wavelengths : numpy.ndarray
        Per-band wavelengths, ``(n_bands,)``.
    reflectance : numpy.ndarray
        Reflectance values, ``(n_pixels, n_bands)``.
    continuum_idx : tuple[numpy.ndarray, numpy.ndarray]
        ``(left_inds, right_inds)`` continuum-window index arrays.
    valid_wavelengths : numpy.ndarray
        Boolean mask of usable channels, ``(n_bands,)``.

    Returns
    -------
    tuple[numpy.ndarray, numpy.ndarray]
        ``(depths, feature_inds)``: continuum-removed reflectance
        ``(n_pixels, n_feature_bands)`` and the band indices it spans.
    """
    left_inds, right_inds = continuum_idx

    left_x, right_x = wavelengths[int(left_inds.mean())], wavelengths[int(right_inds.mean())]
    left_y, right_y = reflectance[:, left_inds].mean(), reflectance[:, right_inds].mean()

    band = np.arange(len(valid_wavelengths))
    feature_inds = np.where((band >= left_inds[0]) & (band <= right_inds[-1]) & valid_wavelengths)[0]

    continuum = interp1d([left_x, right_x], [left_y, right_y], fill_value="extrapolate")(wavelengths[feature_inds])
    depths = 1 - reflectance[:, feature_inds] / continuum
    return depths, feature_inds


def get_continuum_idx(
    wavelengths: np.ndarray,
    feature: tuple[float, float, float, float],
    valid_wavelengths: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Locate the continuum-window band indices for a feature.

    A feature is ``(a, b, c, d)``: the left window spans ``[a, b]`` and the right
    ``[c, d]``. When a window contains no valid channels, it falls back to the single
    nearest band just inside it (first band ``>= b`` on the left, last band ``<= c``
    on the right).

    Parameters
    ----------
    wavelengths : numpy.ndarray
        Per-band wavelengths, ``(n_bands,)``.
    feature : tuple[float, float, float, float]
        ``(a, b, c, d)`` continuum-window bounds.
    valid_wavelengths : numpy.ndarray
        Boolean mask of usable channels, ``(n_bands,)``.

    Returns
    -------
    tuple[numpy.ndarray, numpy.ndarray] or None
        ``(left_inds, right_inds)`` index arrays, or ``None`` if no continuum exists.
    """
    a, b, c, d = feature
    left = np.where(valid_wavelengths & (wavelengths >= a) & (wavelengths <= b))[0]
    right = np.where(valid_wavelengths & (wavelengths >= c) & (wavelengths <= d))[0]

    if left.size == 0:
        nearest = np.where(wavelengths >= b)[0]
        if nearest.size == 0:
            return None
        left = nearest[:1]

    if right.size == 0:
        nearest = np.where(wavelengths <= c)[0]
        if nearest.size == 0:
            return None
        right = nearest[-1:]

    return left, right


def calculate_uncertainty(
    wl: np.ndarray,
    rfl: np.ndarray,
    uncert: np.ndarray,
    lib: np.ndarray,
    features: list[dict],
) -> np.ndarray:
    """Per-pixel uncertainty of the Clark (2003) continuum-normalized band depth.

    The band depth is modelled as ``bd = a * L(w_star)``, where ``L`` is the
    continuum-removed library spectrum and ``a`` is the least-squares scale fitting
    the observed continuum-removed spectrum to ``L``::

        a = L(w_star) / (n * Σ L² − (Σ L)²)

    Treating each band's continuum-removed uncertainty as its reflectance
    uncertainty ``σ`` and assuming bands are independent, the band-depth variance is::

        var = a² * ( n² * Σ(L² σ²) + (Σ L)² * Σ σ² )

    Each ``MLw``/``DLw`` feature contributes ``sqrt(var)``; the features are combined
    as a weighted mean, weighted by the number of bands in each feature.

    Parameters
    ----------
    wl : numpy.ndarray
        Per-band wavelengths in microns, ``(n_bands,)``.
    rfl : numpy.ndarray
        Observed reflectance for the valid pixels, ``(n_pixels, n_bands)``.
    uncert : numpy.ndarray
        Observed reflectance uncertainty, same shape as ``rfl``.
    lib : numpy.ndarray
        Library reference reflectance for the record, ``(n_bands,)``.
    features : list[dict]
        The block's feature definitions.

    Returns
    -------
    numpy.ndarray
        Band-depth uncertainty per pixel, ``(n_pixels,)``.
    """
    good = rfl[0] != -0.01  # deleted channels share the -0.01 sentinel

    uncertainties, weights = [], []
    for feature in features:
        if feature["feature_type"] not in ("MLw", "DLw"):
            continue

        idx = get_continuum_idx(wl, feature["continuum"], good)
        if idx is None:
            continue

        lib_cont, bands = cont_rem(wl, lib[None, :], idx, good)
        lib_cont = np.squeeze(lib_cont)
        sigma = uncert[:, bands]
        n = len(lib_cont)

        a = lib_cont.max() / (n * np.sum(lib_cont**2) - lib_cont.sum() ** 2)
        var = n**2 * (sigma**2 @ lib_cont**2) + lib_cont.sum() ** 2 * np.sum(sigma**2, axis=1)

        uncertainties.append(abs(a) * np.sqrt(var))
        weights.append(n)

    if not uncertainties:
        return np.zeros(len(rfl))

    return np.average(uncertainties, axis=0, weights=weights)
