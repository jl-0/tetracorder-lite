"""tetraconv: pure-Python Tetracorder spectral-library convolution.

A clean-room reimplementation of USGS specpr function 17 (Gaussian high-to-low
resolution convolution).  Given an unconvolved master library (``splib06b``) and a
target EMIT grid from an ENVI header, it builds a complete convolved specpr library.
No convolution recipe is needed -- the spectra, their native wavelength/FWHM grids,
and titles are read directly from the master's own records.

Public entry point: :func:`tetraconv.library.build_library`.
"""

from .convolve import convolve_spectrum, delx_array, effective_width
from .envi import TargetGrid, read_grid
from .library import build_library, index_master
from .specpr import SpecprFile, SpecprWriter

__all__ = [
    "build_library",
    "index_master",
    "convolve_spectrum",
    "delx_array",
    "effective_width",
    "read_grid",
    "TargetGrid",
    "SpecprFile",
    "SpecprWriter",
]
