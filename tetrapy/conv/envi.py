"""Parse the target instrument grid out of an ENVI ``.hdr`` header.

Only the fields that define the output grid are read: ``bands``, ``wavelength``,
and ``fwhm``.  ENVI wavelengths for EMIT are in nanometers; specpr works in
microns, so both arrays are divided by 1000.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

NM_PER_UM = 1000.0


@dataclass
class TargetGrid:
    """Output wavelengths and FWHM (both in microns) for the convolved library."""

    wavelengths_um: np.ndarray
    fwhm_um: np.ndarray

    @property
    def nbands(self) -> int:
        return self.wavelengths_um.size


def _parse_vector(header_text: str, key: str) -> np.ndarray | None:
    """Extract a ``key = { a , b , ... }`` brace-delimited numeric vector."""
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*\{{(.*?)\}}", header_text,
                  re.IGNORECASE | re.DOTALL | re.MULTILINE)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
    return np.asarray([float(p) for p in parts], dtype=np.float64)


def _parse_scalar_int(header_text: str, key: str) -> int | None:
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(\d+)", header_text,
                  re.IGNORECASE | re.MULTILINE)
    return int(m.group(1)) if m else None


def read_grid(hdr_path) -> TargetGrid:
    """Read wavelengths and FWHM from an ENVI header, converted to microns."""
    with open(hdr_path, "r", errors="replace") as fh:
        text = fh.read()

    wl = _parse_vector(text, "wavelength")
    if wl is None:
        raise ValueError(f"{hdr_path}: no 'wavelength' field found")
    fwhm = _parse_vector(text, "fwhm")
    if fwhm is None:
        raise ValueError(f"{hdr_path}: no 'fwhm' field found")

    bands = _parse_scalar_int(text, "bands")
    if bands is not None and (wl.size != bands or fwhm.size != bands):
        raise ValueError(
            f"{hdr_path}: bands={bands} but wavelength has {wl.size} and "
            f"fwhm has {fwhm.size} entries")
    if wl.size != fwhm.size:
        raise ValueError(f"{hdr_path}: wavelength/fwhm length mismatch")

    return TargetGrid(
        wavelengths_um=(wl / NM_PER_UM).astype(np.float64),
        fwhm_um=(fwhm / NM_PER_UM).astype(np.float64),
    )
