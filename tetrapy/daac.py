"""
Convert tetrapy L2B products into LP DAAC-compatible NetCDF4 files.

This is a tetrapy-native port of ``emit-sds-l2b/group_output_conversion.py``. It reads
the mineral / uncertainty products written by :mod:`tetrapy.aggregate`
(``min.{nc,tif}`` and ``minuncert.{nc,tif}``, each a ``(band=4, y, x)`` raster) and
writes the two DAAC products the LP DAAC expects:

    * abundance    -> group 1/2 band depth + mineral id  (+ embedded mineral metadata)
    * uncertainty  -> group 1/2 band depth uncertainty + fit

The NetCDF writing itself is delegated to :mod:`emit_utils.daac_converter`, so this
module carries no dependence on ``emit-sds-l2b``. Because ``emit_utils`` pulls in
``netCDF4`` and ``osgeo.gdal`` (container-only dependencies), those imports are made
lazily inside :func:`build`; importing this module never touches them.

Optional EMIT L1B inputs (``loc``/``glt``/``primary``) enrich the products with a
``location`` group and full acquisition/spatial global attributes; when they are
absent the products are still written with the base global attributes.

Authors: Philip G. Brodrick, philip.brodrick@jpl.nasa.gov (original)
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

import warnings
from rasterio.errors import NotGeoreferencedWarning

# Very spammy, just turn them off
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)

Logger = logging.getLogger(__name__)


# Summary text appended to the base mission summary, one per product.
ABUN_SUMMARY = (
    "This collection contains L2B band depth and geologic identification data. Band depth "
    "is estimated through linear feature matching - see ATBD for details. This collection "
    "includes band depth for both 'Group 1' and 'Group 2' minerals, which frequently "
    "co-occur. The band depth reported is that of the given mineral identified, which is "
    "also reported in a separate band. Geolocation data (latitude, longitude, height) and a "
    "lookup table to project the data are also included."
)

UNCERT_SUMMARY = (
    "This collection contains L2B band depth uncertainty estimates of surface minerals, the "
    "fit quality of each mineral, and geolocation data. Band depth uncertainty is estimated "
    "by propagating reflectance uncertainty through linear feature matching used for "
    "abundance mapping - see ATBD for details. Band depth uncertainty and fit qualities are "
    "provided for both 'Group 1' and 'Group 2' minerals, which frequently co-occur. Fit "
    "quality indicates how well the library-normalized observed spectra match the selected "
    "library spectra. Geolocation data (latitude, longitude, height) and a lookup table to "
    "project the data are also included."
)

# Band layout of the aggregate products, in raster-band order. Each entry is
# (netcdf variable name, netcdf dtype, long name).
ABUN_BANDS = [
    ("group_1_band_depth", "f4", "Group 1 Band Depth"),
    ("group_1_mineral_id", "i2", "Group 1 Mineral ID"),
    ("group_2_band_depth", "f4", "Group 2 Band Depth"),
    ("group_2_mineral_id", "i2", "Group 2 Mineral ID"),
]

UNCERT_BANDS = [
    ("group_1_band_depth_unc", "f4", "Group 1 Band Depth Uncertainty"),
    ("group_1_fit", "f4", "Group 1 Fit"),
    ("group_2_band_depth_unc", "f4", "Group 2 Band Depth Uncertainty"),
    ("group_2_fit", "f4", "Group 2 Fit"),
]

# mineral_metadata group embedded in the abundance product, adapted to the columns of
# the tetrapy reference matrix. Each entry is (netcdf variable name, reference-csv
# column, netcdf dtype). Columns not present in the reference matrix (e.g. ``url``) are
# skipped with a warning.
METADATA_COLUMNS = [
    ("index", "index", "u4"),
    ("record", "record", "u4"),
    ("name", "title", str),
    ("url", "url", str),
    ("group", "group", "u4"),
    ("library", "library", str),
]


def _read_bands(path: str | Path) -> np.ndarray:
    """
    Read an aggregate product raster into a ``(band, y, x)`` array.

    Parameters
    ----------
    path : str or Path
        Path to a ``min``/``minuncert`` product. ``.nc`` is opened with the default
        NetCDF backend; any other extension (e.g. ``.tif``) is opened via ``rasterio``.

    Returns
    -------
    numpy.ndarray
        The ``band_data`` variable as a ``(band, y, x)`` array.
    """
    path = Path(path)
    if path.suffix == ".nc":
        da = xr.open_dataset(path)["band_data"]
    else:
        da = xr.open_dataset(path, engine="rasterio")["band_data"]
    return da.load().values


def _write_product(
    out: str | Path,
    title: str,
    summary: str,
    bands: np.ndarray,
    spec: list[tuple[str, str, str]],
    loc: str | None,
    glt: str | None,
    primary: str | None,
    software_delivery_version: str,
    ref_df: pd.DataFrame | None = None,
) -> None:
    """
    Write one DAAC NetCDF product using the ``emit_utils`` converter helpers.

    Parameters
    ----------
    out : str or Path
        Destination NetCDF path (clobbered if it exists).
    title, summary : str
        Product title and the product-specific summary appended to the mission summary.
    bands : numpy.ndarray
        The ``(4, y, x)`` band stack to write.
    spec : list[tuple[str, str, str]]
        Per-band ``(variable name, dtype, long name)`` in raster-band order.
    loc, glt, primary : str or None
        Optional EMIT L1B location / GLT / primary ENVI files. ``loc``/``glt`` add the
        ``location`` group; ``primary`` enables the full acquisition/spatial global
        attributes (otherwise only the base attributes are written).
    software_delivery_version : str
        Extended build number recorded in the global attributes.
    ref_df : pandas.DataFrame, optional
        Reference matrix; when given, its rows are embedded as the ``mineral_metadata``
        group (abundance product only).
    """
    from netCDF4 import Dataset
    from emit_utils.daac_converter import (
        add_glt,
        add_loc,
        add_variable,
        makeGlobalAttr,
        makeGlobalAttrBase,
    )

    out = Path(out)
    out.parent.mkdir(exist_ok=True, parents=True)

    Logger.info(f"Creating netCDF4 file: {out}")
    nc_ds = Dataset(out, "w", clobber=True, format="NETCDF4")

    # Global attributes: use the full acquisition/spatial set when a primary ENVI is
    # supplied, otherwise fall back to the scene-independent base attributes.
    if primary:
        makeGlobalAttr(nc_ds, primary, software_delivery_version, glt_envi_file=glt)
    else:
        Logger.debug("No primary ENVI supplied; writing base global attributes only")
        makeGlobalAttrBase(nc_ds)

    nc_ds.title = title
    nc_ds.summary = nc_ds.summary + "\n\n" + summary
    nc_ds.sync()

    # Swath dimensions come straight from the aggregate raster shape.
    _, y, x = bands.shape
    nc_ds.createDimension("downtrack", y)
    nc_ds.createDimension("crosstrack", x)

    # add_glt writes into ("ortho_y", "ortho_x"); pre-create them from the GLT raster.
    if glt:
        from osgeo import gdal

        glt_ds = gdal.Open(glt, gdal.GA_ReadOnly)
        nc_ds.createDimension("ortho_y", glt_ds.RasterYSize)
        nc_ds.createDimension("ortho_x", glt_ds.RasterXSize)
    nc_ds.sync()

    if loc:
        Logger.debug("Creating and writing location data")
        add_loc(nc_ds, loc)
    if glt:
        Logger.debug("Creating and writing glt data")
        add_glt(nc_ds, glt)

    Logger.debug("Writing band data")
    for band, (name, dtype, long_name) in zip(bands, spec):
        if dtype == "i2":
            band = band.astype(np.int16)
        add_variable(
            nc_ds, name, dtype, long_name, "unitless", band,
            {"dimensions": ("downtrack", "crosstrack"), "zlib": True, "complevel": 9},
        )
    nc_ds.sync()

    if ref_df is not None:
        Logger.debug("Embedding mineral metadata")
        nc_ds.createDimension("minerals", len(ref_df))
        for name, column, dtype in METADATA_COLUMNS:
            if column not in ref_df.columns:
                Logger.warning(f"Reference matrix has no '{column}' column, skipping mineral_metadata/{name}")
                continue
            if dtype is str:
                data = np.array(ref_df[column]).astype("S")
            else:
                data = np.array(ref_df[column])
            add_variable(nc_ds, f"mineral_metadata/{name}", dtype, column, None, data, {"dimensions": ("minerals",)})
        nc_ds.sync()

    nc_ds.close()
    Logger.debug(f"Successfully created {out}")


def build(
    abun: str,
    abununcert: str,
    out_abun: str,
    out_abununcert: str,
    loc: str | None = None,
    glt: str | None = None,
    primary: str | None = None,
    version: str = "V001",
    software_delivery_version: str = "unknown",
    reference: str | None = None,
) -> None:
    """
    Convert the tetrapy L2B products into the two DAAC NetCDF products.

    \b
    Parameters
    ----------
    abun, abununcert : str
        Paths to the aggregate ``min`` and ``minuncert`` products (``.nc`` or ``.tif``),
        each a ``(band=4, y, x)`` raster as written by :func:`tetrapy.aggregate.build`.
    out_abun, out_abununcert : str
        Destination paths for the abundance and uncertainty DAAC NetCDF products.
    loc, glt : str, optional
        EMIT L1B location and GLT ENVI files. When given, a ``location`` group
        (lon/lat/elev and the GLT lookup table) is added to each product.
    primary : str, optional
        EMIT primary ENVI file carrying the ``emit acquisition ...`` metadata keys. When
        given, full acquisition/spatial global attributes are written; otherwise only the
        base (scene-independent) attributes are.
    version : str, default "V001"
        Version string appended to each product title.
    software_delivery_version : str, default "unknown"
        Extended build number recorded in the global attributes.
    reference : str, optional
        Path to the tetrapy reference matrix CSV. When given, its rows are embedded as
        the ``mineral_metadata`` group on the abundance product (mapping ``title`` to the
        ``name`` variable; columns absent from the matrix, such as ``url``, are skipped).
    """
    ref_df = pd.read_csv(reference) if reference else None

    abun_bands = _read_bands(abun)
    uncert_bands = _read_bands(abununcert)

    _write_product(
        out_abun,
        title=f"EMIT L2B Estimated Mineral Identification and Band Depth 60 m {version}",
        summary=ABUN_SUMMARY,
        bands=abun_bands,
        spec=ABUN_BANDS,
        loc=loc,
        glt=glt,
        primary=primary,
        software_delivery_version=software_delivery_version,
        ref_df=ref_df,
    )

    _write_product(
        out_abununcert,
        title=f"EMIT L2B Estimated Mineral Identification and Band Depth Uncertainty 60 m {version}",
        summary=UNCERT_SUMMARY,
        bands=uncert_bands,
        spec=UNCERT_BANDS,
        loc=loc,
        glt=glt,
        primary=primary,
        software_delivery_version=software_delivery_version,
    )
