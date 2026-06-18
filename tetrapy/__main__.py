import os

import click

from tetrapy import tetra


@click.group
def cli():
    pass


# Shared click options
outp = click.option("-o", "--output", default="/output/tetracorder")
mode = click.option("-m", "--mode", default="cube")
file = click.option("-f", "--file", default="/data/r")


@cli.command(help=tetra.setup_tetrun.__doc__)
@click.option("-v", "--version", default="6.00a")
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


@cli.command
def run(**kwargs):
    """\
    Executes setup then tetrun
    """
    tetra.setup_tetrun(**kwargs)
    tetra.exec_tetrun(**kwargs)
