import os
import logging
from pathlib import Path

import click
from box import Box

from tetrapy import pipeline as pl
from tetrapy.config import load


Logger = logging.getLogger(__name__)
CS = dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
)
Config = click.argument("config")
Section = click.option("-s", "--section", help="Subsection of the yaml to load rather than the whole file")


def init(config: str, section: str, ctx: click.Context) -> Box:
    """
    Load, patch, and interpolate the config, then configure logging.

    Shared by every command: reads the YAML at ``config`` (optionally narrowed to
    ``section``), applies the CLI overrides carried on ``ctx``, resolves ``${...}``
    interpolation, and sets the root log level from ``log.level``.

    Parameters
    ----------
    config : str
        Path to the config YAML file.
    section : str
        Subsection of the config to load, or a falsy value for the whole file.
    ctx : click.Context
        Click context whose extra args supply the dotted ``--key value`` overrides.

    Returns
    -------
    box.Box
        The fully resolved configuration.
    """
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
def run(ctx, **kwargs) -> None:
    """\
    Execute the full tetrapy pipeline from a YAML config
    """
    c = init(ctx=ctx, **kwargs)

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

    if c.daac.enabled:
        pl.daac(c)

    Logger.info("Done")


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
