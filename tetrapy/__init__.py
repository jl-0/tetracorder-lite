import logging
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from tetrapy.config import load


Console = Console(record=True)
Logger = logging.getLogger(__name__)


def init(config: str, section: str, ctx: click.Context):
    """
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
    c = load(config, section, ctx=ctx, interp=True)

    handlers = [
        RichHandler(
            console=Console,
            rich_tracebacks=True,
            tracebacks_suppress=[click],
        )
    ]

    if file:
        file = Path(file)
        file.parent.mkdir(parents=True, exist_ok=True)

        fh = logging.FileHandler(file, mode="w" if c.log.reset else "a")
        fh.setLevel(logging.DEBUG)

        fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
        fmt = logging.Formatter(fmt)
        fh.setFormatter(fmt)

        handlers.append(fh)

    level = c.log.get("level", "INFO").upper()
    level = getattr(logging, level)

    logging.basicConfig(
        level=lvl,
        handlers=handlers,
        format="%(message)s",
        datefmt="[%X]",
    )

    return c
