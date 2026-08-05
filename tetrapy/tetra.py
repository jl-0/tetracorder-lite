import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from tetrapy import convolve as cv
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
    **_
) -> None:
    """\
    Call the tetracorder cmd-setup-tetrun script to initialize a tetracorder run.

    This function sets up the necessary directory structure and configuration files
    for running USGS Tetracorder mineral identification. It invokes the tetracorder
    setup script and applies necessary patches for compatibility.

    \b
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
    file : str, default="/data/r"
        Input reflectance file path to process.
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
    exists = Path(output).exists()
    if rm and exists:
        shutil.rmtree(output)
        exists = False

    assert not exists, "cmd-setup-tetrun requires the output directory to not exist"

    cmd = [
        f"/t1/tetracorder.cmds/tetracorder{version}.cmds/cmd-setup-tetrun",
        output,
        sensor,
        mode,
        rfl,
        *args
    ]

    subprocess.run(cmd)

    # Remove erroneous 'time' command in the script
    path = Path(output) / "cmd.runtet"
    if path.exists():
        path.write_text(
            path.read_text().replace("time", "")
        )

    # v6 cmd file needs to set the geology parameter
    if "6" in version:
        path = Path(f"{output}/cmds.start.t{version}")

        if path.exists():
            lines = path.read_text().splitlines()

            geo = "geology" if geology else "nogeology"
            for i, line in enumerate(lines):
                if line.startswith("mode"):
                    lines.insert(i, geo)
                    break

            path.write_text("\n".join(lines) + "\n")

    if cores:
        path = Path(f"{output}/TETNCPU.txt")
        if path.exists():
            path.write_text(str(cores))

    Logger.info("\nAlternatively, use `tetrapy tetrun` to execute with defaults")


def exec_tetrun(
    output: str = "/output/tetracorder",
    mode: str = "cube",
    rfl: str = "/data/r",
    args: List[str] = ["band", "20", "gif"],
    **_
) -> None:
    """\
    Execute the tetracorder cmd.runtet script to run mineral identification.

    This function runs the tetracorder processing on the input reflectance data
    using the configuration prepared by setup_tetrun. Output is logged to tetrun.log.

    \b
    Parameters
    ----------
    output : str, default="/output/tetracorder"
        Output directory containing the tetracorder setup.
    mode : str, default="cube"
        Tetracorder processing mode, either "cube" or "singlespectrum".
    file : str, default="/data/r"
        Input reflectance file path to process.
    args : List[str], default=["band", "20", "gif"]
        Additional arguments to pass to cmd.runtet script.

    Notes
    -----
    All output from the tetracorder run is captured in tetrun.log within the
    output directory for debugging and verification purposes.
    """
    cmd = [
        "./cmd.runtet",
        mode,
        rfl
    ]

    log = Path(output) / "tetrun.log"
    with log.open("w") as f:
        subprocess.run(cmd, cwd=output, stdout=f, stderr=subprocess.STDOUT)


def parse_variables(file: str) -> Dict[str, str]:
    """
    Parse ==[NAME] value... definitions from a command file.

    This function extracts variable definitions from tetracorder command files,
    which use a special ==[NAME] syntax to define parameters. The values are
    returned as strings to preserve formatting and precision.

    Parameters
    ----------
    file : str
        Path to the command file to parse.

    Returns
    -------
    Dict[str, str]
        Mapping of variable names to their string values (whitespace-stripped).
        If a variable is defined multiple times, the last definition wins.

    Examples
    --------
    Given a file with content:
        ==[THRESHOLD] 0.5
        ==[VALUES] 1.0 2.0 3.0
        ==[THRESHOLD] 0.8

    >>> parse_variables("cmd.file")
    {'THRESHOLD': '0.8', 'VALUES': '1.0 2.0 3.0'}
    """
    text = Path(file).read_text()

    pattern = re.compile(
        r"^==\[(?P<name>[^\]]+)\]\s*(?P<values>[-+0-9.eE\s]+)",
        re.MULTILINE,
    )

    return {
        match["name"]: match["values"].strip()
        for match in pattern.finditer(text)
    }


def patch_cmd_file(output: str, version: str) -> None:
    """
    Create a patched version of the expert command file for EMIT l2b compatibility.

    This function processes tetracorder's expert system command file to make it
    compatible with the EMIT l2b processing pipeline. It substitutes variable
    references and removes duplicate TITLE= directives that cause parsing errors.

    Parameters
    ----------
    output : str
        Path to the tetracorder output directory containing the command files.
    version : str
        Tetracorder version string (e.g., "6.00a") used to locate the correct
        command file (cmd.lib.setup.t{version}#).

    Notes
    -----
    The patched file is written to {output}/cmd.lib.setup.t{version}#.patched.
    Two main transformations are applied:
    1. Variable substitutions: [VAR] references are replaced with their values
       from cmd.lib.setup.variables
    2. TITLE= cleanup: Only title comment lines keep TITLE=; other instances
       are removed to prevent parsing conflicts
    """
    cmd = "cmd.lib.setup"
    ver = f"t{version}5"

    output = Path(output)
    variables = parse_variables(output / f"{cmd}.variables")

    text = Path(output / f"{cmd}.{ver}").read_text()

    # Fix variable substitutions
    text = re.sub(
        r"\[([^\]]+)\]",
        lambda m: variables.get(m[1], m[0]),
        text,
    )

    # Remove all instances of TITLE= that aren't a title line
    text = re.sub(
        r"(?m)^(?!\\#[-=]+.*TITLE=).*?TITLE=",
        "",
        text,
    )

    Path(output / f"{cmd}.{ver}.patched").write_text(text)


def group_aggregator(
    version: str = "6.00a",
    output: str = "/output/tetracorder",
    matrix: str = "/root/tetrapy/data/mineral_grouping_matrix_t6.subset.csv",
    reflib: str = "/root/emit-sds-l2b/Spectral-Library-Reader-master/s06av18a_envi",
    reslib: str = "/root/emit-sds-l2b/Spectral-Library-Reader-master/r06av18a_envi",
    rfl: Optional[str] = None,
    unc: Optional[str] = None,
) -> None:
    """
    Streamline the call to the emit-sds-l2b group_aggregator.py script.

    This function wraps the EMIT L2B group aggregator processing, which takes
    tetracorder's mineral identification results and aggregates them into mineral
    group maps with uncertainty quantification. It automatically patches the expert
    system file if needed.

    \b
    Parameters
    ----------
    version : str, default="6.00a"
        Tetracorder version string used to locate the expert system file.
    output : str, default="/output/tetracorder"
        Path to the tetracorder output directory.
    matrix : str
        Path to the mineral grouping matrix CSV file that defines how individual
        minerals are aggregated into groups.
    reflib : str
        Path to the reference spectral library in ENVI format.
    reslib : str
        Path to the research spectral library in ENVI format.
    rfl : Optional[str], default=None
        Path to the reflectance file used in the tetracorder run. Required for
        uncertainty calculation.
    unc : Optional[str], default=None
        Path to the reflectance uncertainty file. Required for uncertainty
        calculation.

    \b
    Notes
    -----
    Output files are written to {output}/l2b/:
    - agg: Aggregated mineral group map
    - unc: Uncertainty map

    The expert system file is automatically patched for EMIT compatibility if
    not already patched.

    The function runs group_aggregator.py as a subprocess using Python's
    interpreter, which avoids import issues with emit-sds-l2b package structure.
    """
    out = Path(output) / "l2b"
    out.mkdir(exist_ok=True)

    esf = f"cmd.lib.setup.t{version}5.patched"
    if not (Path(output) / esf).exists():
        Logger.info("The expert system file has not been patched, doing so now")
        patch_cmd_file(output, version)

    # Build command arguments
    cmd = [
        sys.executable,  # Use current Python interpreter
        "/root/emit-sds-l2b/group_aggregator.py",
        output,
        matrix,
        str(out / "abun"),
        str(out / "abununcert"),
        "--expert_system_file", esf,
        "--reference_library", reflib,
        "--research_library", reslib,
    ]

    if rfl and unc:
        cmd += [
            "--calculate_uncertainty",
            "--reflectance_file", rfl,
            "--reflectance_uncertainty_file", unc,
        ]

    Logger.info("Calling emit-sds-l2b group_aggregator.py")
    Logger.debug(f"Command:\n{utils.format_args(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=output,
        capture_output=True,
        text=True
    )

    log = out / "group_aggregator.log"
    with log.open("w") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)


def group_output_conversion(
    output: str,
    agg: Optional[str],
    unc: Optional[str],
    loc: str,
    glt: str,
    version: str,
    software_delivery_version: str,
) -> None:
    """
    Convert tetracorder group outputs to EMIT L2B format.

    Wraps the emit-sds-l2b group_output_conversion.py script to package
    mineral abundance and uncertainty maps into standardized EMIT L2B
    product format with metadata.

    \b
    Parameters
    ----------
    output : str
        Output directory path containing l2b subdirectory with results.
    agg : str, optional
        Path to aggregated reflectance file. If None, uses {output}/l2b/agg.
    unc : str, optional
        Path to uncertainty file. If None, uses {output}/l2b/unc.
    loc : str
        Path to location (LOC) file with geographic coordinates.
    glt : str
        Path to geometric lookup table (GLT) file for orthorectification.
    version : str
        Product version identifier (e.g., "1.0.0").
    software_delivery_version : str
        EMIT SDS software version used for processing.

    Notes
    -----
    Expects the following files to exist in {output}/l2b/:
    - abun: mineral abundance ENVI format
    - abununcert: mineral abundance uncertainty ENVI format

    The script will create standardized NetCDF output with embedded metadata.
    """
    out = Path(output) / "l2b"

    # Build command arguments
    cmd = [
        sys.executable,  # Use current Python interpreter
        "/root/emit-sds-l2b/group_output_conversion.py",
        out / "abun.nc",
        out / "abununcert.nc",
        agg if agg else out / "abun",
        unc if unc else out / "abununcert",
        loc,
        glt,
        version,
        software_delivery_version,
    ]

    assert cmd[4].exists(), f"Missing mineral abundance envi: {cmd[4]}"
    assert cmd[5].exists(), f"Missing mineral abununcert envi: {cmd[5]}"
    cmd = [str(c) for c in cmd]

    Logger.info("Calling emit-sds-l2b group_output_conversion.py")
    Logger.debug(f"Command:\n{utils.format_args(cmd)}")

    log = out / "group_output_conversion.log"
    with log.open("w") as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=output)



def get_sensor(text: str) -> str:
    """
    Auto-detect the sensor name from a restart file.

    Parameters
    ----------
    text : str
        Restart file content

    Returns
    -------
    str
        Sensor ID extracted from the restart file

    Raises
    ------
    ValueError
        If sensor name cannot be detected

    Examples
    --------
    >>> text = Path('restart_files/r1-emitc').read_text()
    >>> sensor = detect_sensor_from_restart(text)
    >>> print(sensor)
    'emitc'
    """
    # Try to extract from irfl= line (most reliable)
    match = re.search(r'^irfl=r1-(\w+)', text, re.MULTILINE)
    if match:
        return match.group(1)

    # Fallback: try to extract from iwfl= line
    match = re.search(r'^iwfl=/sl1/usgs/rlib06/r06(\w+)', text, re.MULTILINE)
    if match:
        return match.group(1)

    # Fallback: try to extract from iyfl= line
    match = re.search(r'^iyfl=/sl1/usgs/library06\.conv/s06(\w+)', text, re.MULTILINE)
    if match:
        return match.group(1)

    raise ValueError("Could not detect sensor name from restart file")


def get_channels(hdr: str) -> int:
    """
    Extract the number of channels from an ENVI header file.

    Parameters
    ----------
    hdr : str
        Path to ENVI header file (.hdr)

    Returns
    -------
    int
        Number of spectral channels/bands

    Raises
    ------
    ValueError
        If channels/bands field not found in header
    FileNotFoundError
        If header file doesn't exist
    """
    text = Path(hdr).read_text()

    # Try to find 'bands' field (preferred)
    match = re.search(r'^bands\s*=\s*(\d+)', text, re.IGNORECASE | re.MULTILINE)
    if match:
        return int(match.group(1))

    # Fallback: count wavelength array elements
    match = re.search(r'^wavelength\s*=\s*\{(.*?)\}', text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if match:
        wl = [w.strip() for w in match.group(1).replace('\n', ' ').split(',') if w.strip()]
        return len(wl)

    raise ValueError(f"Could not find 'bands' or 'wavelength' field in {path}")


def update_restart(
    text: str,
    reflib: str,
    reslib: str,
    chns: int = None,
    ) -> str:
    """
    Update the restart file values for the newly convolved libraries

    Parameters
    ----------
    text : str
        Original restart file content
    reflib : str
        Path to reference library file
    reslib : str
        Path to research library
    chns : int, default=None
        New channel count. If None, keeps existing value.

    Returns
    -------
    str
        Updated restart file content
    """
    pack = {"w": reslib, "y": reflib}
    for t, lib in pack.items():
        # Research library path (iwfl)
        text = re.sub(
            rf'^(i{t}fl=)\S+(\s*)$',
            rf'\1{reslib}\2',
            text,
            flags=re.MULTILINE
        )

        # Update the protection value
        size = os.path.getsize(lib)
        records = size // 1536
        protection = -records
        text = re.sub(
            rf'^(iprt{t}=\s+).*(  # device protection {t})$',
            rf'\1{protection}\2',
            text,
            flags=re.MULTILINE
        )

    # Channel count (nchans)
    if chns is not None:
        text = re.sub(
            r'^(nchans=\s+)\d+(\s+#.*)$',
            lambda m: f"{m.group(1)}{chns:>12}{m.group(2)}",
            text,
            flags=re.MULTILINE
        )

    return text


def get_protection(file: str) -> int:
    """
    Calculations the protection value of a file
    """
    size = os.path.getsize(file)
    records = size // 1536 - 1
    protection = -records
    return protection


def find_deleted_channels(
    wavelengths: NDArray[np.float64],
    bad_regions: Optional[List[Tuple[float, float]]] = [
        # Default atmospheric absorption regions
        ( 000,  380), # Below visible
        ( 940,  960), # H2O
        (1100, 1160), # H2O
        (1350, 1500), # H2O
        (1800, 1980), # H2O
        (2500, 3000), # Thermal/low SNR
    ],
) -> List[int]:
    """
    Identify channel indices that fall within bad spectral regions.

    Parameters
    ----------
    wavelengths : NDArray[np.float64]
        Wavelength array in micrometers
    bad_regions : list of tuple, optional
        List of (min_wl, max_wl) tuples defining bad regions in micrometers.
        If None, uses default atmospheric absorption bands.

    Returns
    -------
    list of int
        1-indexed channel numbers to delete

    Examples
    --------
    >>> wavelengths = np.linspace(0.38, 2.5, 285)
    >>> deleted = find_deleted_channels(wavelengths)
    >>> print(deleted[:5])  # First few deleted channels
    [1, 2, 3, 4, 5]
    """
    deleted = []
    for i, wl in enumerate(wavelengths, start=1):
        for min_wl, max_wl in bad_regions:
            if min_wl <= wl <= max_wl:
                deleted.append(i)
                break

    return deleted


def format_deleted_ranges(channels: List[int]) -> str:
    """
    Convert a list of channel numbers to range string format.

    Parameters
    ----------
    channels : list of int
        List of channel numbers (1-indexed)

    Returns
    -------
    str
        Space-separated range string (e.g., "1-4 107-113 150-165")

    Examples
    --------
    >>> format_deleted_ranges([1, 2, 3, 4, 10, 11, 12, 20])
    '1-4  10-12  20'
    """
    if not channels:
        return ""

    channels = sorted(set(channels))
    ranges = []
    start = channels[0]
    end = channels[0]

    for ch in channels[1:]:
        if ch == end + 1:
            end = ch
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}t{end}")
            start = end = ch

    # Add final range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}t{end}")

    return "  ".join(ranges)


def read_wavelengths(hdr: str) -> NDArray[np.float64]:
    """
    Extract wavelength array from an ENVI header file.

    Parameters
    ----------
    hdr_path : str
        Path to ENVI .hdr file

    Returns
    -------
    NDArray[np.float64]
        Wavelength array in micrometers

    Raises
    ------
    FileNotFoundError
        If header file doesn't exist
    ValueError
        If wavelength field is not found or cannot be parsed
    """
    text = Path(hdr).read_text()

    # Find wavelength array
    match = re.search(
        r'wavelength\s*=\s*\{(.*?)\}',
        text,
        re.DOTALL | re.IGNORECASE
    )
    if not match:
        raise ValueError(f"No wavelength field found in {hdr_path}")

    # Parse wavelengths
    wls = match.group(1).replace('\n', ' ').strip()
    try:
        wavelengths = np.array([float(w.strip()) for w in wls.split(',') if w.strip()])
    except ValueError as e:
        raise ValueError(f"Failed to parse wavelengths: {e}")

    # Check units and convert if needed
    units_match = re.search(r'wavelength\s+units\s*=\s*(\w+)', text, re.IGNORECASE)
    if units_match:
        units = units_match.group(1).lower()
        if units in ['nanometers', 'nm']:
            wavelengths = wavelengths / 1000.0  # Convert nm to µm
        elif units not in ['micrometers', 'um', 'microns', 'µm']:
            Logger.warning(f"Unknown wavelength units '{units}', assuming micrometers")

    return wavelengths


def make_deleted_file(path, name, hdr):
    """
    Attempts to find deleted channels
    """
    wavelengths = read_wavelengths(hdr)

    # Find deleted channels
    deleted = find_deleted_channels(wavelengths)

    # Format as ranges
    ranges = format_deleted_ranges(deleted)

    # Write file
    file = path / "DELETED.channels" / f"delete_{name}"
    file.write_text(f"{ranges} c # {name}")


def make_deleted_file(path, name, *_, **__):
    """
    Just takes the template as-is
    """
    text = Path("tetrapy/templates/delete_channels.tmpl").read_text()
    text = text.format(name=name)
    (path / "DELETED.channels" / f"delete_{name}").write_text(text)


def make_datasets_file(path, name):
    """
    """
    text = "\n".join([f"data=    {name}", f"restart= r1-{name}"])
    (path / "DATASETS" / name).write_text(text)


def make_colors_file(path, name):
    """
    """
    text = Path("tetrapy/templates/color.tmpl").read_text()
    (path / "COLOR.channels" / f"color-{name}").write_text(text)


def make_disable_file(path, name):
    """
    """
    text = Path("tetrapy/templates/disable.tmpl").read_text()
    (path / "DISABLE" / name).write_text(text)


def make_restart_file(path, name, hdr, reflib, reslib):
    """
    """
    file = path / "restart_files" / f"r1-{name}"
    text = Path("tetrapy/templates/restart_file.tmpl").read_text()
    text = text.format(
        name   = name,
        nchans = get_channels(hdr),
        reflib_full       = reflib,
        reflib_short      = reflib.name[:8],
        reflib_protection = get_protection(reflib),
        reslib_full       = reslib,
        reslib_short      = reslib.name[:8],
        reslib_protection = get_protection(reslib),
    )
    file.write_text(text)


def make_convolution(lib, file, hdr, output, noconv):
    """
    """
    Logger.info(f"Convolving {lib}: {file}")

    output /= f"{lib}"
    envi = output.with_suffix(".envi")

    if not noconv:
        if output.exists():
            output.unlink()

        conv.build_library(
            master_path = str(file),
            out_path = str(output),
            hdr_path = str(hdr),
            family = lib,
            log = Logger.debug,
        )

        Logger.debug(f"  Saved to: {output}")

        cv.export_envi(
            str(output),
            str(envi)
        )

    return output


def convolve(
    reflib: str,
    reslib: str,
    rfl: str,
    version: str = "6.00a",
    output: str = "/conv",
    name: str = "tetrapy",
    integrate: bool = True,
    noconv: bool = False,
) -> Dict[str, str]:
    """
    Build convolved libraries from research and reference inputs with a recipe and integrate into tetracorder.

    This command takes research (reslib) and reference (reflib) unconvolved libraries,
    convolves them using a specified recipe onto the target instrument grid from the
    scene's ENVI header, and integrates them into tetracorder. Convolved outputs are
    saved to [output]/l2b/.

    For each library, this function:
    1. Convolves the master library using the provided recipe
    2. Writes the specpr result to {output}/l2b/
    3. Copies it to the tetracorder library path under /root/sl1
    4. Exports an ENVI-format copy to {output}/l2b/ for downstream processing

    Parameters
    ----------
    reflib : str
        Path to the unconvolved reference library (splib06b) in specpr format.
    reslib : str
        Path to the unconvolved research library (sprlb06b) in specpr format.
    recipe : str
        Path to the convolution recipe file (.cmds or .csv) that defines which
        spectra to convolve and their configurations.
    file : str, default="/data/r"
        Path to the EMIT reflectance file (or its .hdr) providing the target
        wavelength/FWHM grid for convolution.
    output : str, default="/output"
        Output root directory. Convolved specpr + ENVI libraries are written
        under {output}/l2b/.

    Returns
    -------
    Dict[str, str]
        Mapping of library role ("reference", "research") to their output paths
        in {output}/l2b/.

    Raises
    ------
    FileNotFoundError
        If a master library, recipe file, or ENVI header is missing.

    Notes
    -----
    The convolved libraries are installed at paths that tetracorder expects:
    - Reference library: /root/sl1/usgs/library06.conv/s06emitc
    - Research library: /root/sl1/usgs/rlib06/r06emitc

    ENVI exports are suitable for use with the L2B group aggregator (tetrapy gagg).
    """
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    hdr = Path(rfl).with_suffix(".hdr")

    if not (reflib := Path(reflib)).exists():
        raise FileNotFoundError(f"Reference library not found: {reflib}")
    if not (reslib := Path(reslib)).exists():
        raise FileNotFoundError(f"Research library not found: {reslib}")
    if not hdr.exists():
        raise FileNotFoundError(f"ENVI header not found: {hdr}")

    # Research Library
    # sprlb = reslib
    reslib = make_convolution(
        lib  = "reslib",
        file = reslib,
        hdr  = hdr,
        output = out,
        noconv = noconv
    )

    # Reference Library
    # splib = reflib
    reflib = make_convolution(
        lib  = "reflib",
        file = reflib,
        hdr  = hdr,
        output = out,
        noconv = noconv,
    )

    # Integrate these into Tetracorder
    if integrate:
        path = Path(f"/root/tetracorder/tetracorder.cmds/tetracorder{version}.cmds")
        make_colors_file(path, name)
        make_disable_file(path, name)
        make_datasets_file(path, name)
        make_deleted_file(path, name, hdr)
        make_restart_file(path, name, hdr, reslib=reslib, reflib=reflib)
