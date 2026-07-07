import os

import click

from tetrapy import tetra
from tetrapy import convolve


@click.group()
def cli():
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
file = click.option("-f", "--file", default="/data/r")


@cli.command(help=tetra.setup_tetrun.__doc__)
@vers
@outp
@click.option("-s", "--sensor", default="emit_c")
@mode
@file
@click.option("-g", "--geology", is_flag=True)
@click.option("-c", "--cores", type=int, default=os.cpu_count())
@click.option("-a", "--args", nargs=9, default=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"])
@click.option("--rm", is_flag=True)
def setup(**kwargs):
    tetra.setup_tetrun(**kwargs)


@cli.command(help=tetra.exec_tetrun.__doc__)
@outp
@mode
@file
@click.option("-a", "--args", nargs=3, default=["band", "20", "gif"])
def tetrun(**kwargs):
    tetra.exec_tetrun(**kwargs)


@cli.command(help=tetra.patch_cmd_file.__doc__)
@vers
@outp
def patch(**kwargs):
    tetra.patch_cmd_file(**kwargs)


@cli.command(help="Setup then run tetracorder (the default container action).")
@click.option("-v", "--version", default="6.00a")
@outp
@click.option("-s", "--sensor", default="emit_c")
@mode
@file
@click.option("-g", "--geology", is_flag=True)
@click.option("-c", "--cores", type=int, default=os.cpu_count())
@click.option("-a", "--args", nargs=9, default=["1", "-T", "-20", "80", "C", "-P", ".5", "1.5", "bar"])
@click.option("--rm", is_flag=True)
def run(**kwargs):
    tetra.setup_tetrun(**kwargs)
    tetra.exec_tetrun(**kwargs)


@cli.command("convolve", help=convolve.build_convolved_library.__doc__)
@file
@click.option("-o", "--output", default="/output/s06emit_convolved",
              help="Output convolved-library path (specpr + ENVI)")
@click.option("-s", "--sensor", default="emitc",
              help="Sensor suffix — selects template s06<s> from the image")
@click.option("--master", default="/spectral-lib/splib06b",
              help="Unconvolved master library path")
def convolve_cmd(file, output, sensor, master):
    sl1 = "/root/sl1/usgs"
    template = f"{sl1}/library06.conv/s06{sensor}"
    envi_header = f"{file}.hdr" if not file.endswith(".hdr") else file
    convolve.build_convolved_library(
        master=master, template=template, output=output, envi_header=envi_header
    )
    convolve.export_envi(output, f"{output}_envi")


@cli.command("validate", help=convolve.compare_libraries.__doc__)
@click.argument("a")
@click.argument("b")
def validate_cmd(a, b):
    convolve.compare_libraries(a, b)
