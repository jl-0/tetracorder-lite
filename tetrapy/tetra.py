import os
import shutil
from pathlib import Path

import subprocess


def setup_tetrun(
    version="6.00a",
    output="/output/tetracorder",
    sensor="emit_c",
    mode="cube",
    file="/data/r",
    geology=False,
    cores=os.cpu_count(),
    args=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"],
    rm=False,
    **_
):
    """\
    Calls the tetracorder cmd-setup-tetrun script

    \b
    Parameters
    ----------
    version : str, default="6.00a"
        Tetracorder version to use
    output : str, default="/output/tetracorder"
        Output directory. Must not exist
    sensor : str, default="emit_c"
        Sensor to use
    mode : str, default="cube"
        Tetracorder mode, either "cube" or "singlespectrum"
    file : str, default="/data/r"
        File to process
    geology : bool, default=False
        If v6 tetracorder, enables geology. Defaults to nogeology
    cores : int, default=os.cpu_count()
        Cores to set in TETNCPU.txt
    args : list, default=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"]
        Arguments to pass to cmd-setup-tetrun
    rm : bool, default=False
        Removes the output directory to do a clean setup
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

    print("\nAlternatively, use `tetrapy tetrun` to execute with defaults")


def exec_tetrun(
    output="/output/tetracorder",
    mode="cube",
    file="/data/r",
    args=["band", "20", "gif"],
    **_
):
    """\
    Calls the tetracorder cmd.runtet script

    \b
    Parameters
    ----------
    output : str, default="/output/tetracorder"
        Output directory
    mode : str, default="cube"
        Tetracorder mode, either "cube" or "singlespectrum"
    file : str, default="/data/r"
        File to process
    args : list, default=["band", "20", "gif"]
        Arguments to pass to cmd.runtet
    """
    cmd = [
        "./cmd.runtet",
        mode,
        file
    ]

    subprocess.run(cmd, cwd=output)
