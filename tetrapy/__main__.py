import os

import click

from tetrapy import tetra
from tetrapy import convolve


@click.group
def cli():
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


@cli.command
def run(**kwargs):
    """\
    Executes setup then tetrun
    """
    tetra.setup_tetrun(**kwargs)
    tetra.exec_tetrun(**kwargs)


@cli.command("convolve", help=convolve.build_convolved_library.__doc__)
@click.option("-m", "--master", default="/data/splib06b",
              help="Unconvolved master library (specpr format)")
@click.option("-t", "--template", default="/data/template",
              help="Existing convolved library to reuse structure/indexing from")
@click.option("-o", "--output", default="/output/library")
@click.option("-e", "--envi-header", default=None,
              help="EMIT reflectance .hdr for the target wavelength/FWHM grid "
                   "(omit to reuse the template's grid)")
def convolve_cmd(**kwargs):
    convolve.build_convolved_library(**kwargs)


@cli.command("validate", help=convolve.compare_libraries.__doc__)
@click.argument("a")
@click.argument("b")
def validate_cmd(a, b):
    convolve.compare_libraries(a, b)
