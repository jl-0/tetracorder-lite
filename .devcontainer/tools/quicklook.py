"""
Render the demo's result imagery from a finished ``tetrapy run``.

Runs inside the tetracorder-lite image, which already carries numpy, matplotlib
and xarray, so the devcontainer itself needs no Python environment of its own.

Produces, into --out:
  rfl_rgb.png        true-colour quicklook of the input reflectance
  group1.png         group 1 mineral identifications, with a legend
  group2.png         group 2 mineral identifications, with a legend
  group1_depth.png   group 1 band depth
  group2_depth.png   group 2 band depth
  results.json       per-group statistics the results page reads

Mineral ID values in the aggregate product are ``index`` values from the
reference matrix (tetrapy/data/v6.00a6.csv); 0 means "nothing identified".
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap, to_hex

# Minerals beyond this many per group are folded into a single "other" class so
# the legend stays readable. They are still counted in the totals.
LEGEND_LIMIT = 12
OTHER = "#8a8a8a"
NODATA = -9999


def read_envi(prefix: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an ENVI cube as (lines, samples, bands) plus its wavelengths."""
    fields: dict[str, str] = {}
    for match in re.finditer(r"^([\w ]+?)\s*=\s*(\{.*?\}|.*?)$", Path(f"{prefix}.hdr").read_text(), re.M | re.S):
        fields[match.group(1).strip().lower()] = match.group(2).strip()

    samples, lines, bands = (int(fields[k]) for k in ("samples", "lines", "bands"))
    endian = ">" if fields.get("byte order", "0").strip() == "1" else "<"
    dtype = np.dtype(endian + {1: "u1", 2: "i2", 3: "i4", 4: "f4", 5: "f8", 12: "u2", 13: "u4"}[int(fields["data type"])])

    interleave = fields.get("interleave", "bsq").strip().lower()
    shapes = {"bil": (lines, bands, samples), "bip": (lines, samples, bands), "bsq": (bands, lines, samples)}
    cube = np.memmap(prefix, dtype=dtype, mode="r", offset=int(fields.get("header offset", 0)), shape=shapes[interleave])
    axes = {"bil": (0, 2, 1), "bip": (0, 1, 2), "bsq": (1, 2, 0)}[interleave]

    wl = np.array([float(v) for v in fields["wavelength"].strip("{}").split(",")])
    return np.asarray(cube.transpose(axes), dtype=np.float32), wl


def stretch(band: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linear 2-98 percentile stretch to [0, 1], with invalid pixels at 0."""
    if not valid.any():
        return np.zeros_like(band)
    lo, hi = np.percentile(band[valid], (2, 98))
    if hi <= lo:
        return np.zeros_like(band)
    return np.clip((band - lo) / (hi - lo), 0, 1) * valid


def render_rgb(cube: np.ndarray, wl: np.ndarray, out: Path) -> dict:
    """True-colour composite from the red, green and blue EMIT channels."""
    idx = [int(np.argmin(abs(wl - target))) for target in (640.0, 550.0, 470.0)]
    valid = (cube[:, :, idx[0]] != NODATA) & np.isfinite(cube[:, :, idx[0]])
    rgb = np.dstack([stretch(cube[:, :, i], valid) for i in idx])

    fig, ax = plt.subplots(figsize=(6, 6), dpi=140)
    ax.imshow(rgb, interpolation="nearest")
    ax.set_title("L2A reflectance (true colour)", fontsize=11)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    return {"lines": int(cube.shape[0]), "samples": int(cube.shape[1]), "bands": int(cube.shape[2]),
            "valid_pixels": int(valid.sum())}


def render_group(ids: np.ndarray, depth: np.ndarray, titles: dict[int, str], group: int, out: Path) -> dict:
    """Render one group's mineral-ID map and band-depth map; return its stats."""
    values, counts = np.unique(ids[ids > 0].astype(int), return_counts=True)
    order = np.argsort(-counts)
    values, counts = values[order], counts[order]

    # Two qualitative colormaps back to back give 32 distinct hues, comfortably
    # more than LEGEND_LIMIT, without inventing a palette.
    palette = [to_hex(c) for c in list(plt.get_cmap("tab20").colors) + list(plt.get_cmap("tab20b").colors)]
    shown = values[:LEGEND_LIMIT]
    colors = {int(v): palette[i % len(palette)] for i, v in enumerate(shown)}

    # Index 0 is the background (nothing identified); everything past the legend
    # limit collapses onto a single "other" index.
    lut = np.zeros(int(values.max()) + 1 if values.size else 1, dtype=int)
    for i, v in enumerate(shown):
        lut[int(v)] = i + 1
    for v in values[LEGEND_LIMIT:]:
        lut[int(v)] = len(shown) + 1

    indexed = lut[np.clip(ids.astype(int), 0, len(lut) - 1)]
    cmap = ListedColormap(["#101010"] + [colors[int(v)] for v in shown] + [OTHER])

    fig, ax = plt.subplots(figsize=(6, 6), dpi=140)
    ax.imshow(indexed, cmap=cmap, vmin=0, vmax=len(shown) + 1, interpolation="nearest")
    ax.set_title(f"Group {group} mineral identifications", fontsize=11)
    ax.axis("off")

    # No legend inside the image: the results page renders one as a table, and
    # an external matplotlib legend widens the figure so much that the map
    # itself ends up far smaller than the band-depth map beside it.
    fig.tight_layout()
    fig.savefig(out / f"group{group}.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=140)
    image = ax.imshow(np.where(depth > 0, depth, np.nan), cmap="magma", interpolation="nearest")
    ax.set_title(f"Group {group} band depth", fontsize=11)
    ax.axis("off")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(out / f"group{group}_depth.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    total = int(ids.size)
    stats = {
        "group": group,
        "classified": int((ids > 0).sum()),
        "total": total,
        "percent": round(100.0 * (ids > 0).sum() / total, 1) if total else 0.0,
        "materials": int(values.size),
        "top": [{"id": int(v), "title": titles.get(int(v), f"id {v}"), "pixels": int(c),
                 "color": colors.get(int(v), OTHER)} for v, c in zip(shown, counts[:LEGEND_LIMIT])],
    }
    # Everything past the legend limit shares one colour on the map, so the
    # table needs a row saying so rather than silently omitting those pixels.
    if values.size > LEGEND_LIMIT:
        stats["other"] = {"pixels": int(counts[LEGEND_LIMIT:].sum()),
                          "materials": int(values.size - LEGEND_LIMIT), "color": OTHER}
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rfl", type=Path, required=True, help="input reflectance ENVI prefix")
    parser.add_argument("--agg", type=Path, required=True, help="aggregate product (agg.nc)")
    parser.add_argument("--reference", type=Path, default=Path("/root/tetrapy/data/v6.00a6.csv"))
    parser.add_argument("--out", type=Path, required=True, help="directory to write imagery into")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    cube, wl = read_envi(args.rfl)
    results = {"scene": render_rgb(cube, wl, args.out / "rfl_rgb.png"), "groups": []}

    reference = pd.read_csv(args.reference)
    titles = dict(zip(reference["index"].astype(int), reference["title"].astype(str)))

    # Imported here so a missing aggregate product still leaves the RGB behind.
    import xarray as xr

    with xr.open_dataset(args.agg) as ds:
        for group in (1, 2):
            ids, depth = f"group_{group}_mineral_id", f"group_{group}_band_depth"
            if ids not in ds:
                continue
            results["groups"].append(
                render_group(ds[ids].values, ds[depth].values, titles, group, args.out)
            )

    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
