import os
import logging
from pathlib import Path

import click

from tetrapy import aggregate
from tetrapy import tetra


logging.basicConfig(level=logging.DEBUG)
Logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """tetracorder-lite CLI.

    Run USGS Tetracorder (v6) mineral identification or rebuild the convolved
    spectral library — all containerized.

    \b
    Default (no subcommand): setup + run tetracorder on the mounted input.
    """
    pass


# Shared click options
vers = click.option("-v", "--version", default="6.00a")
outp = click.option("-o", "--output", default="/output/tetracorder")
mode = click.option("-m", "--mode", default="cube")
file = click.option("-f", "--rfl", default="/data/r")


@cli.command(help=tetra.setup_tetrun.__doc__)
@vers
@outp
@click.option("-s", "--sensor", default="tetrapy")
@mode
@file
@click.option("-g", "--geology", is_flag=True)
@click.option("-c", "--cores", type=int, default=os.cpu_count())
@click.option("-a", "--args", nargs=9, default=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"])
@click.option("--rm", is_flag=True)
def setup(**kwargs) -> None:
    tetra.setup_tetrun(**kwargs)


@cli.command(help=tetra.exec_tetrun.__doc__)
@outp
@mode
@file
@click.option("-a", "--args", nargs=3, default=["band", "20", "gif"])
def tetrun(**kwargs) -> None:
    tetra.exec_tetrun(**kwargs)


cli.add_command(aggregate.main)


@cli.command(help=tetra.group_output_conversion.__doc__)
@outp
@click.option("-a", "--agg")
@click.option("-u", "--unc")
@click.option("-l", "--loc", required=True)
@click.option("-g", "--glt", required=True)
@click.option("-v", "--version", required=True)
@click.option("-s", "--software_delivery_version", required=True)
def goc(**kwargs) -> None:
    tetra.group_output_conversion(**kwargs)


@cli.command("convolve", help=tetra.convolve.__doc__)
@vers
@file
@click.option("-rl", "--reflib", default="/root/tetracorder/sl1/usgs/library06.conv/splib06b")
@click.option("-sl", "--reslib", default="/root/tetracorder/sl1/usgs/library06.conv/sprlb06b")
@click.option("-r", "--recipe")
@click.option("-nc", "--noconv", is_flag=True)
def convolve(**kwargs) -> None:
    tetra.convolve(**kwargs)


from tetrapy.config import load


@cli.command(
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    )
)
@click.pass_context
@click.argument("config")
@click.option("-s", "--section", help="Subsection of the yaml to load rather than the whole file")
def run(ctx, config, section):
    """\
    Execute the full tetrapy pipeline
    """
    C = load(config, section, ctx=ctx, interp=True)

    if C.convolve.enabled:
        Logger.info("Executing Convolve")
        tetra.convolve(
            version = C.tetracorder.version,
            rfl     = C.data.rfl,
            reflib  = C.convolve.reflib,
            reslib  = C.convolve.reslib,
        )

    if C.setup.enabled:
        Logger.info("Executing Setup")
        tetra.setup_tetrun(
            version = C.tetracorder.version,
            mode    = C.tetracorder.mode,
            rfl     = C.data.rfl,
            output  = C.output,
            sensor  = C.setup.sensor,
            geology = C.setup.geology,
            args    = C.setup.args,
            rm      = C.setup.autoremove,
        )

    # Save config after setup (tetracorder initializes the directory)
    if (out := Path(config.output)).exists():
        config.to_yaml(filename=out / "config.yml")

    if C.tetrun.enabled:
        Logger.info("Executing Tetrun")
        tetra.exec_tetrun(
            mode    = C.tetracorder.mode,
            rfl     = C.data.rfl,
            output  = C.output,
            args    = C.tetrun.args,
        )

    if C.aggregate.enabled:
        Logger.info("Executing Aggregate")
        aggregate.build(
            tetracorder = C.aggregate.tetracorder,
            output      = C.aggregate.output,
            reflib      = C.aggregate.reflib,
            reslib      = C.aggregate.reslib,
            output_as   = C.aggregate.output_as,
        )

    Logger.info("Done")
