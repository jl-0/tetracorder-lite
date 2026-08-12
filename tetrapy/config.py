import ast
import logging
import re
from typing import Any, Optional, Union

import click
from box import Box, BoxList


Logger = logging.getLogger(__name__)
Interp = re.compile(r"\${([^}]+)}")


def load(
    config: str,
    section: Optional[str] = None,
    ctx: Optional[click.Context] = None,
    interp: bool = True,
) -> Box:
    """
    Loads a config from a yaml file

    Parameters
    ----------
    config : str
        File path to the config.yml
    section : str
        Returns only this subsection of the config
    ctx : obj, default=None
        Click context object
    interp : bool, default=True
        Calls interpolate on the config before returning

    Returns
    -------
    config : Box
        Loaded configuration using Box
    """
    config = Box.from_yaml(filename=config, default_box=True)

    if section:
        config = config[section]

    if ctx:
        patch(config, ctx)

    if interp:
        interpolate(config)

    return config


def patch(config: Box, ctx: click.Context) -> Box:
    """
    Patch the config in place with dotted ``--key value`` options from the CLI.

    Scans ``ctx.args`` for ``--dotted.key value`` pairs, parses each value as a
    Python literal (falling back to a string), and merges them into ``config`` using
    Box dot-notation, so e.g. ``--tetrun.args '["band", 10]'`` overrides a nested key.

    Parameters
    ----------
    config : box.Box
        The loaded config to override.
    ctx : click.Context
        Click context whose ``args`` carry the extra ``--key value`` overrides.

    Returns
    -------
    box.Box
        The same ``config``, updated with the overrides.
    """
    args = ctx.args

    # Convert dot notation to dict
    conv = Box(default_box=True, box_dots=True)

    i = 0
    while i < len(args):
        arg = args[i]

        if arg.startswith("--"):
            key = arg[2:]
            val = args[i + 1]
            try:
                val = ast.literal_eval(val)
            except:
                Logger.warning(f"Failed to parse the value and will default as string: --{key} {val}")

            Logger.debug(f"Overriding {key} with {val!r}")
            conv[key] = val

            i += 2
        else:
            i += 1

    # Override config with new converted values
    return config.merge_update(conv)


def interp(val: str, rel: Box, full: Box) -> Any:
    """
    Interpolate ``${...}`` references in a single string against the config.

    Format: ``${.key}`` or ``${key}``. A leading dot makes the key relative to the
    subsection the original value lives in (``rel``); otherwise it resolves from the
    top of the config (``full``). After substitution the result is parsed as a Python
    literal when possible, so a fully-substituted string can become e.g. an int/list.

    Parameters
    ----------
    val : str
        The value to interpolate.
    rel : box.Box
        The subsection used to resolve relative (``${.key}``) references.
    full : box.Box
        The full config used to resolve absolute (``${key}``) references.

    Returns
    -------
    Any
        The interpolated value; a literal (int/list/...) when it parses as one,
        otherwise the substituted string (or ``val`` unchanged if it had no refs).
    """
    if matches := Interp.findall(val):
        for key in matches:
            if key.startswith("."):
                Logger.debug("Using relative pathing")
                ref = rel
            else:
                Logger.debug("Using full pathing")
                ref = full

            new = ref[key]
            if isinstance(new, str) and "${" in new:
                new = interp(new, rel, full) # TODO: Recursion guard

            fmt = "${" + key + "}"
            val = val.replace(fmt, str(new))
            Logger.debug(f"Replaced {fmt!r} with {new!r}")
        try:
            val = ast.literal_eval(val)
        except:
            pass
        Logger.debug(f"New value: {val!r}")

    return val


def interpolate(
    box: Union[Box, BoxList],
    orig: Optional[Box] = None,
    rel: Optional[Box] = None,
) -> None:
    """
    Recursively interpolate every ``${...}`` reference in a config tree in place.

    Walks ``box`` (a Box or BoxList), replacing each string value via :func:`interp`.
    ``orig`` is the full config used for absolute references; ``rel`` is the current
    subsection used for relative ones. Both default to ``box`` itself on the first
    call and are threaded down as the walk descends into subsections.

    Parameters
    ----------
    box : box.Box or box.BoxList
        The config node to interpolate; mutated in place.
    orig : box.Box, optional
        Full config for absolute references. Defaults to ``box`` on the first call.
    rel : box.Box, optional
        Subsection for relative references. Defaults to ``orig``.
    """
    if orig is None:
        orig = Box(box, box_dots=True, default_box=True)

    if rel is None:
        rel = orig

    if isinstance(box, BoxList):
        items = enumerate(box)
    else:
        rel = Box(box, box_dots=True, default_box=True)
        items = box.items()

    for key, val in items:
        if isinstance(val, (Box, BoxList)):
            interpolate(val, orig, rel)
        elif isinstance(val, str):
            box[key] = interp(val, rel, orig)
