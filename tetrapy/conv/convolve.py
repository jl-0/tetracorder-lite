"""Gaussian high-to-low resolution convolution, ported from specpr function 17.

This is a faithful reimplementation of the USGS ratfor convolution used to build the
EMIT spectral libraries, driven with the same options the recipe uses (``n`` =
normalize, ``g`` = Gaussian bandpasses).  The source lives in
``tetracorder/specpr/src.specpr/fcn17-19/``:

    f17.r      driver: gmode==1 loop over output channels (lines 306-322)
    gfiles.r   effective bandwidth per output channel      (lines 257-303)
    ggauss.r   Gaussian bandpass from center + FWHM
    convol.r   channel-width-weighted, normalized resample
    delx.r     local input channel spacing

For each output channel the effective Gaussian width is
``sqrt(FWHM_out**2 - FWHM_in**2)`` (quadrature correction to the instrument's own
resolution), a Gaussian bandpass is generated on the native grid, and the output
sample is the normalized, channel-width-weighted sum of the input reflectance under
that bandpass.

The specpr "deleted point" sentinel is ``-1.23e34``; anything below ``DEL_THRESH``
is treated as deleted.
"""

from __future__ import annotations

import numpy as np

DELETED = -1.23e34
DEL_THRESH = -1.0e30      # values below this are the deleted sentinel
FOUR_LN2 = 2.772589       # 4*ln(2); Gaussian is parameterised by FWHM
DEFAULT_TLIM = 0.1e-7     # bandpass values below this are ignored (f17 default)
EXP_FLOOR = -82.0         # exp(tmp) underflows to 0 below this (ggauss.r)


def _is_deleted(a: np.ndarray) -> np.ndarray:
    return a < DEL_THRESH


def delx_array(wav: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Local channel spacing for every input channel (port of ``delx.r``).

    ``valid[j]`` marks channels whose wavelength, bandpass and spectrum are all
    non-deleted.  For an interior channel the spacing is half the gap between the
    nearest valid neighbours on each side; edge channels use the one-sided gap.
    Channels that resolve to no valid neighbour get 0.

    Because the generated Gaussian bandpass is never itself the deleted sentinel,
    the neighbour search depends only on the wavelength/spectrum validity, which is
    the same for every output channel -- so this is computed once per spectrum.
    """
    n = wav.size
    z = np.zeros(n, dtype=np.float64)
    if n < 2:
        return z

    valid_idx = np.flatnonzero(valid)
    if valid_idx.size == 0:
        return z

    def next_valid_above(i: int) -> int:
        for j in range(i + 1, n):
            if valid[j]:
                return j
        return -1

    def next_valid_below(i: int) -> int:
        for j in range(i - 1, -1, -1):
            if valid[j]:
                return j
        return -1

    wdel = _is_deleted(wav)
    for i in range(n):
        if i == 0:
            j = next_valid_above(0)
            if j < 0 or wdel[i] or wdel[j]:
                z[i] = 0.0
            else:
                z[i] = abs(wav[i] - wav[j])
        elif i == n - 1:
            j = next_valid_below(i)
            if j < 0 or wdel[i] or wdel[j]:
                z[i] = 0.0
            else:
                z[i] = abs(wav[i] - wav[j])
        else:
            j = next_valid_above(i)   # first valid above
            if j < 0:                 # none above -> one-sided, anchored at i
                k = next_valid_below(i)
                z[i] = 0.0 if k < 0 or wdel[i] or wdel[k] else abs(wav[i] - wav[k])
                continue
            k = next_valid_below(i)   # first valid below
            if k < 0:                 # none below -> one-sided, anchored at i
                z[i] = 0.0 if wdel[i] or wdel[j] else abs(wav[i] - wav[j])
                continue
            if wdel[j] or wdel[k]:
                z[i] = 0.0
            else:
                z[i] = abs(wav[j] - wav[k]) * 0.5
    return z


def effective_width(out_wave: np.ndarray, out_fwhm: np.ndarray,
                    in_wave: np.ndarray, in_fwhm: np.ndarray):
    """Per-output-channel Gaussian center and width (port of ``gfiles.r`` 257-303).

    Returns ``(centers, widths, deleted)``.  For each output channel the nearest
    input wavelength ``conind`` is found; the effective width is the quadrature
    difference ``sqrt(out_fwhm**2 - in_fwhm[conind]**2)``.  If the output bandwidth
    is not wider than the input, the channel is degenerate: width collapses to
    ``in_fwhm[conind]*0.01`` and the center snaps to ``in_wave[conind]``.  Output
    channels outside the input wavelength range are marked deleted.

    Note: ``wavmin``/``wavmax`` are taken over the *raw* input array including any
    ``-1.23e34`` deleted sentinels, exactly as ``gfiles.r`` (lines 260-267) does.
    A grid with leading deleted points therefore has ``wavmin == -1.23e34``, so the
    out-of-range test never fires for it; the nearest-input ``conind`` search still
    skips sentinels naturally because their distance to any real center is huge.
    """
    wavmin = float(np.min(in_wave))
    wavmax = float(np.max(in_wave))

    centers = out_wave.astype(np.float64).copy()
    widths = out_fwhm.astype(np.float64).copy()
    deleted = np.zeros(out_wave.size, dtype=bool)

    for i in range(out_wave.size):
        c = centers[i]
        if wavmin < c < wavmax:
            conind = int(np.argmin(np.abs(in_wave - c)))
            in_bw = in_fwhm[conind]
            if widths[i] <= in_bw:
                widths[i] = in_bw * 0.01
                centers[i] = in_wave[conind]
            else:
                widths[i] = np.sqrt(widths[i] ** 2 - in_bw ** 2)
        else:
            deleted[i] = True
    return centers, widths, deleted


def _gauss_weights(centers: np.ndarray, widths: np.ndarray,
                   in_wave: np.ndarray) -> np.ndarray:
    """Gaussian bandpass matrix (n_out x n_in), port of ``ggauss.r``.

    ``gauss = exp(-4 ln2 / width**2 * (in_wave - center)**2)``, with deleted input
    wavelengths and underflowing exponents (``tmp < -82``) set to 0.

    Fallback (ggauss.r lines 132-140): if *every* weight for an output channel
    underflowed to zero (``ngaus == 0``) -- e.g. an output center that lies far
    outside the input grid -- the single nearest non-deleted input channel is given
    weight 1.0.  Convolution then returns that edge channel's reflectance, which is
    how the shipped library extrapolates output channels beyond a spectrum's native
    coverage.
    """
    del_in = _is_deleted(in_wave)
    w = np.where(widths <= 1.0e-10, 1.0e-10, widths)
    ax = -FOUR_LN2 / (w ** 2)                        # (n_out,)
    diff = in_wave[None, :] - centers[:, None]        # (n_out, n_in)
    tmp = ax[:, None] * diff ** 2
    gauss = np.where(tmp < EXP_FLOOR, 0.0, np.exp(tmp))
    gauss[:, del_in] = 0.0

    # ngaus == 0 fallback: nearest non-deleted channel gets weight 1.0
    all_zero = ~gauss.any(axis=1)
    if all_zero.any():
        masked = np.where(del_in[None, :], np.inf, np.abs(diff))
        ichmin = masked.argmin(axis=1)
        for i in np.flatnonzero(all_zero):
            gauss[i, ichmin[i]] = 1.0
    return gauss


def convolve_spectrum(in_wave: np.ndarray, in_fwhm: np.ndarray,
                      in_spec: np.ndarray, out_wave: np.ndarray,
                      out_fwhm: np.ndarray, nmode: int = 1,
                      tlim: float = DEFAULT_TLIM) -> np.ndarray:
    """Convolve one native-resolution spectrum onto the target grid.

    Combines ``gfiles`` -> ``ggauss`` -> ``convol`` for all output channels.  With
    ``nmode == 1`` (the recipe default) the result is the normalized,
    channel-width-weighted average of the input reflectance under each Gaussian
    bandpass.  Output channels with no valid support get the deleted sentinel.
    """
    in_wave = np.asarray(in_wave, dtype=np.float64)
    in_fwhm = np.asarray(in_fwhm, dtype=np.float64)
    in_spec = np.asarray(in_spec, dtype=np.float64)
    out_wave = np.asarray(out_wave, dtype=np.float64)
    out_fwhm = np.asarray(out_fwhm, dtype=np.float64)

    n = in_wave.size
    if not (in_fwhm.size == n and in_spec.size == n):
        raise ValueError("input wavelength/fwhm/spectrum length mismatch")

    del_wav = _is_deleted(in_wave)
    del_spec = _is_deleted(in_spec)
    col_valid = ~(del_wav | del_spec)                 # (n_in,)

    # channel spacing z_j -- independent of output channel (see delx_array)
    z = delx_array(in_wave, col_valid)

    centers, widths, out_deleted = effective_width(out_wave, out_fwhm,
                                                    in_wave, in_fwhm)

    gauss = _gauss_weights(centers, widths, in_wave)  # (n_out, n_in)

    # convol.r: skip channels with bandpass < tlim or deleted wave/spectrum
    weights = np.where(gauss < tlim, 0.0, gauss)
    weights[:, ~col_valid] = 0.0

    xx = weights * z[None, :]                          # t(j) * delx
    s = xx @ in_spec                                   # sum r(j)*xx
    nrm = xx.sum(axis=1)                               # sum xx

    out = np.full(out_wave.size, DELETED, dtype=np.float64)
    if nmode == 1:
        good = (~out_deleted) & (nrm > 0.0) & (s != 0.0)
        out[good] = s[good] / nrm[good]
    else:
        good = (~out_deleted) & (s != 0.0)
        out[good] = s[good]

    # convol.r deletes an output channel if any contributing spacing is absurd
    if np.any(z[col_valid] > 1.0e10):
        bad = (weights > 0) & (z[None, :] > 1.0e10)
        out[np.any(bad, axis=1)] = DELETED

    return out.astype(np.float32)
