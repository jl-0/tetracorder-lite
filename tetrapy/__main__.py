import os
import logging
from pathlib import Path

import click
from box import Box

from tetrapy import (
    init,
    pipeline as pl
)


Logger = logging.getLogger(__name__)
CS = dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
)
Config = click.argument("config")
Section = click.option("-s", "--section", help="Subsection of the yaml to load rather than the whole file")


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
def run(ctx, **kwargs) -> None:
    """\
    Execute the full tetrapy pipeline from a YAML config
    """
    c = init(ctx=ctx, **kwargs)
    pl.run(c)


@cli.command(context_settings=CS, help=pl.export_matrix.__doc__)
@click.pass_context
@Config
@Section
def export_matrix(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.export_matrix(c)


@cli.command(context_settings=CS, help=pl.convolve.__doc__)
@click.pass_context
@Config
@Section
def convolve(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.convolve(c)


@cli.command(context_settings=CS, help=pl.sensor.__doc__)
@click.pass_context
@Config
@Section
def sensor(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.sensor(c)


@cli.command(context_settings=CS, help=pl.setup.__doc__)
@click.pass_context
@Config
@Section
def setup(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.setup(c)


@cli.command(context_settings=CS, help=pl.tetrun.__doc__)
@click.pass_context
@Config
@Section
def tetrun(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.tetrun(c)


@cli.command(context_settings=CS, help=pl.aggregate.__doc__)
@click.pass_context
@Config
@Section
def aggregate(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.aggregate(c)


@cli.command(context_settings=CS, help=pl.daac.__doc__)
@click.pass_context
@Config
@Section
def daac(ctx, **kwargs) -> None:
    c = init(ctx=ctx, **kwargs)
    pl.daac(c)


@cli.command(context_settings=CS)
@click.pass_context
@Config
@Section
def preview(ctx, **kwargs) -> None:
    """\
    Previews the final, interpolated configuration
    """
    c = init(ctx=ctx, **kwargs)
    Logger.info(f"Configuration:\n{c.to_yaml()}")


if __name__ == "__main__":
    cli()
