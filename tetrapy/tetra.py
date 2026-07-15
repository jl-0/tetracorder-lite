import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from tetrapy import utils


Logger = logging.getLogger(__name__)


def setup_tetrun(
    version: str = "6.00a",
    output: str = "/output/tetracorder",
    sensor: str = "emit_c",
    mode: str = "cube",
    file: str = "/data/r",
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

    assert not exists, "cmd-setup-tetrun requires the output directory to not exist"

    cmd = [
        f"/t1/tetracorder.cmds/tetracorder{version}.cmds/cmd-setup-tetrun",
        output,
        sensor,
        mode,
        file,
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
    file: str = "/data/r",
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
        file
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
        command file (cmd.lib.setup.t{version}2).

    Notes
    -----
    The patched file is written to {output}/cmd.lib.setup.t{version}2.patched.
    Two main transformations are applied:
    1. Variable substitutions: [VAR] references are replaced with their values
       from cmd.lib.setup.variables
    2. TITLE= cleanup: Only title comment lines keep TITLE=; other instances
       are removed to prevent parsing conflicts
    """
    cmd = "cmd.lib.setup"
    ver = f"t{version}2"

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
    script_path: Optional[str] = None,
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

    esf = f"cmd.lib.setup.t{version}2.patched"
    if not (Path(output) / esf).exists():
        Logger.info("The expert system file has not been patched, doing so now")
        patch_cmd_file(output, version)

    # Build command arguments
    cmd = [
        sys.executable,  # Use current Python interpreter
        "/root/emit-sds-l2b/group_aggregator.py",
        output,
        matrix,
        str(out / "agg"),
        str(out / "unc"),
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
