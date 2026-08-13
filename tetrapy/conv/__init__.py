"""
Pure-Python Tetracorder spectral-library convolution.

Reimplements USGS specpr function 17 (Gaussian high-to-low resolution convolution):
given an unconvolved master library and a scene's target grid from an ENVI header, it
builds a complete convolved specpr library. No convolution recipe is needed -- the
spectra, their native wavelength/FWHM grids, and titles are read from the master.

Public entry point: :func:`build_library`.
"""

from tetrapy.conv.convolve import Convolver
from tetrapy.conv.library import TargetGrid, build_library, index_master, read_grid
from tetrapy.conv.specpr import SpecprFile, SpecprWriter

__all__ = [
    "build_library",
    "read_grid",
    "index_master",
    "TargetGrid",
    "Convolver",
    "SpecprFile",
    "SpecprWriter",
]
