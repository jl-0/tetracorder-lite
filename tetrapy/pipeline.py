"""
Pipeline stages for ``tetrapy run``.

Each function here is one stage of the ``tetrapy run`` pipeline. They take the
loaded config (a :class:`box.Box`) and dispatch to the module that does the work,
so this file is the single place that maps config keys onto the underlying calls.
The individual stages are also exposed as standalone ``tetrapy`` subcommands (see
:mod:`tetrapy.__main__`), which reuse these functions' docstrings as their
``--help`` text.

Stage order, as run by ``tetrapy run``:
``export_matrix`` -> ``convolve`` -> ``sensor`` -> ``setup`` -> ``tetrun`` ->
``aggregate`` -> ``daac``. Each stage is gated by its own ``enabled`` flag in the
config.
"""

import logging
import subprocess
from pathlib import Path

from box import Box
from rich.progress import Progress

from tetrapy import (
    Console,
    utils
)


Logger = logging.getLogger(__name__)


@utils.log_elapse
def run(c: Box) -> None:
    """
    """
    pl = globals()
    steps = [
        "export_matrix",
        "convolve",
        "sensor",
        "setup",
        "tetrun",
        "aggregate",
        "daac",
    ]
    steps = [step for step in steps if c[step].enabled]

    with Progress(*Progress.get_default_columns(), console=Console) as progress:
        task = progress.add_task("Executing pipeline", total=len(steps))
        for i, step in enumerate(steps):
            Console.rule(style="dim")
            progress.update(task, description=f"Executing: {step}")
            pl[step](c)
            progress.advance(task)

    # REVIEW: Is this the most appropriate location?
    try:
        subprocess.run(['chmod', '-R', 'ugo+rwX,o-w', c.output.tetracorder], check=True)
        subprocess.run(['chmod', '-R', 'ugo+rwX,o-w', c.output.tetrapy], check=True)
    except:
        Logger.exception("Failed to change output file permissions, you may need to manually update them")


@utils.log_elapse
def export_matrix(c: Box) -> None:
    """
    Decode the tetracorder expert system and export its material matrix to CSV.

    Reads the expert-system command file for the configured tetracorder version and
    writes one row per mapped material (see :class:`tetrapy.tetracorder.TetraDecoder`)
    to ``export_matrix.file``, restricted to ``export_matrix.groups`` and
    ``export_matrix.columns``.
    """
    from tetrapy.tetracorder import TetraDecoder

    tetracorder = Path(f"{c.tetracorder.root}/tetracorder.cmds/tetracorder{c.tetracorder.version}.cmds")

    TetraDecoder(
        path        = tetracorder,
        groups      = c.export_matrix.groups,
        raise_casts = False,
    ).export_csv(
        file      = c.export_matrix.file,
        columns   = c.export_matrix.columns,
        reference = c.export_matrix.reference,
    )


@utils.log_elapse
def convolve(c: Box) -> None:
    """
    Convolve the reference + research master libraries onto the scene's grid.

    Reads the target wavelength/FWHM grid from the reflectance ENVI header
    (``{data.rfl}.hdr``) and Gaussian-convolves the unconvolved reference
    (``convolve.reflib``) and research (``convolve.reslib``) master libraries onto
    it, writing convolved specpr libraries to ``convolve.output.{reflib,reslib}``
    for the downstream sensor/aggregate stages.
    """
    from tetrapy import tetra

    tetra.convolve(
        rfl     = c.data.rfl,
        reflib  = c.convolve.reflib,
        reslib  = c.convolve.reslib,
        out_ref = c.convolve.output.reflib,
        out_res = c.convolve.output.reslib,
    )


@utils.log_elapse
def sensor(c: Box) -> None:
    """
    Integrate the convolved library into the tetracorder command tree.

    Wires the convolved libraries (referenced by ``sensor.reflib`` / ``sensor.reslib``)
    into the tetracorder command tree for the sensor named ``sensor.name`` by writing
    its restart, deleted-channels, enable/disable, and color files, so the subsequent
    setup/tetrun stages run against the freshly convolved library.
    """
    from tetrapy import sensor

    tetracorder = Path(f"{c.tetracorder.root}/tetracorder.cmds/tetracorder{c.tetracorder.version}.cmds")

    sensor.build(
        path   = tetracorder,
        sensor = c.sensor,
        rfl    = c.data.rfl,
    )


@utils.log_elapse
def setup(c: Box) -> None:
    """
    Configure a tetracorder run (``cmd-setup-tetrun``).

    Invokes the tetracorder setup script to initialize the output directory for the
    configured version/mode/sensor and scene reflectance, applying the post-setup
    patches (geology flag, CPU count, ``cmd.runtet`` fixups). Any pre-existing
    output directory contents are cleared first (except ``logs/``), which the
    setup script requires to be absent.
    """
    from tetrapy import tetra

    tetra.setup_tetrun(
        tetracorder = c.tetracorder.root,
        version     = c.tetracorder.version,
        mode        = c.tetracorder.mode,
        rfl         = c.data.rfl,
        output      = c.output.tetracorder,
        sensor      = c.tetracorder.sensor,
        geology     = c.setup.geology,
        args        = c.setup.args,
    )


@utils.log_elapse
def tetrun(c: Box) -> None:
    """
    Execute a previously-configured tetracorder run (``cmd.runtet``).

    Runs the ``cmd.runtet`` script prepared by the setup stage against the scene
    reflectance, capturing all output to ``{output}/tetrun.log``.
    """
    from tetrapy import tetra

    tetra.exec_tetrun(
        davinci = c.tetracorder.davinci,
        mode    = c.tetracorder.mode,
        rfl     = c.data.rfl,
        output  = c.output.tetracorder,
        args    = c.tetrun.args,
    )


@utils.log_elapse
def aggregate(c: Box) -> None:
    """
    Aggregate tetracorder outputs into L2B mineral / uncertainty products.

    Decodes the group 1 / group 2 material outputs from the tetracorder run at
    ``aggregate.tetracorder`` and combines them into the L2B mineral and uncertainty
    stacks, written to ``aggregate.output`` in each of the ``aggregate.output_as``
    formats (NetCDF and/or GeoTIFF). The convolved specpr libraries
    (``aggregate.reflib`` / ``aggregate.reslib``) supply reference spectra for the
    band-depth uncertainty calculation.
    """
    from tetrapy import aggregate

    aggregate.build(
        tetracorder   = c.aggregate.tetracorder,
        reflib        = c.aggregate.reflib,
        reslib        = c.aggregate.reslib,
        reference     = c.aggregate.reference,
        out_min       = c.aggregate.out_min,
        out_minuncert = c.aggregate.out_minuncert,
        rfl           = c.data.rfl,
        rfluncert     = c.data.rfluncert,
    )
