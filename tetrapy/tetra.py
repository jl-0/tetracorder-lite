"""
Tetracorder workflow: setup, execution, and library convolution.

Thin drivers around the vendored tetracorder command scripts and the pure-Python
convolution in :mod:`tetrapy.conv`:

- :func:`setup_tetrun` — initialize a run via ``cmd-setup-tetrun`` (plus the
  post-setup patches).
- :func:`exec_tetrun` — execute the configured run via ``cmd.runtet``.
- :func:`convolve` / :func:`make_convolution` — convolve the reference and research
  master libraries onto a scene's grid, writing convolved specpr libraries.

Wiring a convolved library into the command tree is handled separately by
:mod:`tetrapy.sensor`.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from numpy.typing import NDArray

from tetrapy import conv
from tetrapy import utils


Logger = logging.getLogger(__name__)


def setup_tetrun(
    version: str = "6.00a",
    output: str = "/output/tetracorder",
    sensor: str = "emit_c",
    mode: str = "cube",
    rfl: str = "/data/r",
    geology: bool = False,
    cores: Optional[int] = os.cpu_count(),
    args: List[str] = ["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"],
    rm: bool = False,
) -> None:
    """
    Call the tetracorder cmd-setup-tetrun script to initialize a tetracorder run.

    This function sets up the necessary directory structure and configuration files
    for running USGS Tetracorder mineral identification. It invokes the tetracorder
    setup script and applies necessary patches for compatibility.

    Parameters
    ----------
    version : str, default="6.00a"
        Tetracorder version to use (e.g., "6.00a", "5.00").
    output : str, default="/output/tetracorder"
        Output directory path. Must not exist unless rm=True.
    sensor : str, default="emit_c"
        Sensor identifier for spectral library selection (e.g., "emit_c", "aviris").
    mode : str, default="cube"
        Tetracorder processing mode, either "cube" or "singlespectrum".
    rfl : str, default="/data/r"
        Input reflectance file path to process (the data, not the .hdr).
    geology : bool, default=False
        If True and using v6 tetracorder, enables geology mode. Otherwise uses nogeology.
    cores : Optional[int], default=os.cpu_count()
        Number of CPU cores to use. Written to TETNCPU.txt.
    args : List[str], default=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"]
        Additional arguments to pass to cmd-setup-tetrun script.
    rm : bool, default=False
        If True, removes the output directory before setup for a clean start.

    Raises
    ------
    AssertionError
        If output directory already exists and rm=False.

    Notes
    -----
    The function performs several post-processing steps after running the setup script:
    - Removes erroneous 'time' command from cmd.runtet script
    - Sets geology/nogeology parameter for v6 tetracorder
    - Configures CPU core count in TETNCPU.txt
    """
    out = Path(output)
    if rm and out.exists():
        shutil.rmtree(output)

    if out.exists():
        raise ValueError("Tetracorder's cmd-setup-tetrun requires the output directory to not exist")

    cmd = [
        "bash",
        f"/t1/tetracorder.cmds/tetracorder{version}.cmds/cmd-setup-tetrun",
        output,
        sensor,
        mode,
        rfl,
        *args
    ]

    subprocess.run(cmd, check=True)

    # Remove erroneous 'time' command in the script
    path = out / "cmd.runtet"
    if path.exists():
        path.write_text(
            path.read_text().replace("time", "")
        )

    # v6 cmd file needs to set the geology parameter
    if "6" in version:
        path = out / f"cmds.start.t{version}"
        text = path.read_text()
        path.write_text(
            text.replace("GGGGGGEOLOGY", "geology" if geology else "nogeology")
        )

    if cores:
        path = out / "TETNCPU.txt"
        if path.exists():
            path.write_text(str(cores))

    Logger.info("\nAlternatively, use `tetrapy tetrun` to execute with defaults")


def exec_tetrun(
    output: str = "/output/tetracorder",
    mode: str = "cube",
    rfl: str = "/data/r",
    args: List[str] = ["band", "20", "gif"],
    davinci: bool = True
) -> None:
    """
    Execute the tetracorder cmd.runtet script to run mineral identification.

    This function runs the tetracorder processing on the input reflectance data
    using the configuration prepared by setup_tetrun. Output is logged to tetrun.log.

    Parameters
    ----------
    output : str, default="/output/tetracorder"
        Output directory containing the tetracorder setup.
    mode : str, default="cube"
        Tetracorder processing mode, either "cube" or "singlespectrum".
    rfl : str, default="/data/r"
        Input reflectance file path to process (the data, not the .hdr).
    args : List[str], default=["band", "20", "gif"]
        Additional arguments to pass to cmd.runtet script.
    davinci : bool, default=True
        If False, DaVinci entries are stripped from ``PATH`` before running, so the
        run uses the plain tetracorder tooling instead.

    Notes
    -----
    All output from the tetracorder run is captured in tetrun.log within the
    output directory for debugging and verification purposes.
    """
    env = os.environ.copy()
    if not davinci:
        env["PATH"] = ":".join([
            path
            for path in env["PATH"].split(":")
            if "davinci" not in path
        ])

    cmd = [
        "bash",
        "cmd.runtet",
        mode,
        rfl
    ]

    log = Path(output) / "tetrun.log"
    with log.open("w") as f:
        subprocess.run(cmd,
            cwd = output,
            env = env,
            stdout = f,
            stderr = subprocess.STDOUT,
            check = True,
        )


def make_convolution(
    lib: str,
    file: Union[str, Path],
    rfl: Union[str, Path],
    output: Path,
) -> Path:
    """
    Convolve one master library onto a scene grid, writing a convolved specpr library.

    Builds the convolved specpr library at ``{output}`` via
    :func:`tetrapy.conv.build_library`.

    Parameters
    ----------
    lib : str
        Library role/name, used as the output stem (e.g. ``"reflib"`` / ``"reslib"``).
    file : str or Path
        Path to the unconvolved master library (specpr format).
    rfl : str or Path
        Path to the scene reflectance raster supplying the target grid.
    output : Path
        Output path the convolved specpr library is written to.

    Returns
    -------
    Path
        Path to the convolved specpr library (``output``).
    """
    Logger.info(f"Convolving {lib}: {file}")

    conv.build_library(
        master_path = str(file),
        out_path = str(output),
        rfl = str(rfl),
        log = Logger.debug,
    )

    Logger.debug(f"  Saved to: {output}")

    return output


def convolve(
    rfl: str,
    reflib: str,
    reslib: str,
    out_ref: str = "/conv/reflib",
    out_res: str = "/conv/reslib",
) -> None:
    """
    Convolve the reference and research master libraries onto a scene's grid.

    Takes the unconvolved reference (``reflib``) and research (``reslib``) master
    libraries and convolves each onto the target instrument grid read from the
    scene's ENVI header (``{rfl}.hdr``), writing a convolved specpr library for each.
    These libraries are read directly by the downstream aggregator.

    Wiring the convolved libraries into the tetracorder command tree is a separate
    step, handled by :mod:`tetrapy.sensor` (the ``sensor`` pipeline stage).

    Parameters
    ----------
    reflib : str
        Path to the unconvolved reference library (splib06b) in specpr format.
    reslib : str
        Path to the unconvolved research library (sprlb06b) in specpr format.
    rfl : str
        Path to the EMIT reflectance file (the data, not the .hdr). Its companion
        ``.hdr`` supplies the target wavelength/FWHM grid for convolution.
    out_ref : str, default="/conv/reflib"
        Output path for the convolved reference specpr library.
    out_res : str, default="/conv/reslib"
        Output path for the convolved research specpr library.

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If a master library or the scene's ENVI header is missing.

    Notes
    -----
    The convolved specpr libraries are read directly by the L2B aggregator
    (``tetrapy aggregate``).
    """
    if not (reflib := Path(reflib)).exists():
        raise FileNotFoundError(f"Reference library not found: {reflib}")
    if not (reslib := Path(reslib)).exists():
        raise FileNotFoundError(f"Research library not found: {reslib}")

    # Research Library
    # sprlb = reslib
    reslib = make_convolution(
        lib  = "reslib",
        file = reslib,
        rfl  = rfl,
        output = out_res,
    )

    # Reference Library
    # splib = reflib
    reflib = make_convolution(
        lib  = "reflib",
        file = reflib,
        rfl  = rfl,
        output = out_ref,
    )
