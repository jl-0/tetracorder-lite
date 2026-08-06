import os
import logging
from pathlib import Path

import click

from tetrapy import pipeline as pl
from tetrapy.config import load


Logger = logging.getLogger(__name__)
CS = dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
)
Config = click.argument("config")
Section = click.option("-s", "--section", help="Subsection of the yaml to load rather than the whole file")


def init(config, section, ctx):
    config = load(config, section, ctx=ctx, interp=True)

    lvl = getattr(logging, config.log.get("level", "INFO"))
    logging.basicConfig(level=lvl)

    return config


@click.group()
def cli() -> None:
    """\
    Tetracorder-lite is a containerized Tetracorder trimmed to only the essentials
    """
    pass


@cli.command(context_settings=CS)
@click.pass_context
@Config
@Section
def run(**kwargs):
    """\
    Execute the full tetrapy pipeline from a YAML config
    """
    c = init(**kwargs)

    if c.export_matrix.enabled:
        pl.export_matrix(c)

    if c.convolve.enabled:
        pl.convolve(c)

    if c.sensor.enabled:
        pl.sensor(c)

    if c.setup.enabled:
        pl.setup(c)

    # Save config after setup (tetracorder initializes the directory)
    if (out := Path(c.output)).exists():
        c.to_yaml(filename=out / "config.yml")

    if c.tetrun.enabled:
        pl.tetrun(c)

    if c.aggregate.enabled:
        pl.aggregate(c)

    Logger.info("Done")


@cli.command(context_settings=CS, help=pl.export_matrix.__doc__)
@click.pass_context
@Config
@Section
def export_matrix(**kwargs):
    c = init(**kwargs)
    pl.export_matrix(c)


@cli.command(context_settings=CS, help=pl.convolve.__doc__)
@click.pass_context
@Config
@Section
def convolve(**kwargs):
    c = init(**kwargs)
    pl.convolve(c)


@cli.command(context_settings=CS, help=pl.sensor.__doc__)
@click.pass_context
@Config
@Section
def sensor(**kwargs):
    c = init(**kwargs)
    pl.sensor(c)


@cli.command(context_settings=CS, help=pl.setup.__doc__)
@click.pass_context
@Config
@Section
def setup(**kwargs):
    c = init(**kwargs)
    pl.setup(c)


@cli.command(context_settings=CS, help=pl.tetrun.__doc__)
@click.pass_context
@Config
@Section
def tetrun(**kwargs):
    c = init(**kwargs)
    pl.tetrun(c)


@cli.command(context_settings=CS, help=pl.aggregate.__doc__)
@click.pass_context
@Config
@Section
def aggregate(**kwargs):
    c = init(**kwargs)
    pl.aggregate(c)


if __name__ == "__main__":
    cli()
