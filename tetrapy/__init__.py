"""
tetracorder-lite: Containerized USGS Tetracorder mineral identification.

This package provides a Python interface to run USGS Tetracorder (v6) mineral
identification and rebuild convolved spectral libraries. All processing is
containerized for reproducibility and ease of deployment.

Modules
-------
tetra
    Core tetracorder workflow functions (setup, execution, expert system patching,
    group aggregation).
convolve
    Pure Python spectral library convolution (no Fortran/specpr dependencies).
utils
    Utility functions for command formatting and argument processing.

Examples
--------
Run tetracorder mineral identification:
    >>> from tetrapy import tetra
    >>> tetra.setup_tetrun(output="/output/tetracorder", sensor="emit_c")
    >>> tetra.exec_tetrun(output="/output/tetracorder")

Build a convolved spectral library:
    >>> from tetrapy import convolve
    >>> convolve.build_convolved_library(
    ...     master="/spectral-lib/splib06b",
    ...     template="/root/sl1/usgs/library06.conv/s06emitc",
    ...     output="/output/s06emit_convolved",
    ...     envi_header="/data/r.hdr"
    ... )
"""

__version__ = "0.1.0"
__all__ = ["tetra", "convolve", "utils"]
