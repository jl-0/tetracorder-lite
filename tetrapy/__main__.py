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


@cli.command("convol-inputs", help=convolve.write_convol_inputs.__doc__)
@file
@click.option("--waves-out", default="waves.txt")
@click.option("--resol-out", default="resol.txt")
def convol_inputs(**kwargs):
    convolve.write_convol_inputs(**kwargs)


@cli.command("convolve", help=convolve.build_convolved_library.__doc__)
@file
@click.option("-l", "--library-path", default="/data/splib06b")
@outp
@click.option("-n", "--name", default="semcalx")
@click.option("-v", "--version", default="a")
@click.option("-t", "--title", default="EMIT")
@click.option("--fwhm-record", default="12")
def convolve_cmd(**kwargs):
    convolve.build_convolved_library(**kwargs)
