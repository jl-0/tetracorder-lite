import ast
import logging
import re

from box import Box, BoxList


Logger = logging.getLogger(__name__)
Interp = re.compile(r"\${([^}]+)}")


def load(config, section=None, ctx=None, interp=True):
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


def patch(config, ctx):
    """
    Patches the config with options from the CLI, such as "--log.level debug"
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


def interp(val: str, rel: Box, full: Box):
    """
    Interpolates flags in a string with values from the config

    Format: ${.key} or ${key}
    If the key starts with a dot, it is relative to the subsection the original key is in
    If not, it uses the full section to find the key
    """
    if matches := Interp.findall(val):
        for k in matches:
            r = "${" + k + "}"
            if k.startswith("."):
                v = rel[k]
                Logger.debug("Using relative pathing")
            else:
                v = full[k]
                Logger.debug("Using full pathing")

            val = val.replace(r, str(v))
            Logger.debug(f"Replaced {r!r} with {v!r}")
        try:
            val = ast.literal_eval(val)
        except:
            pass
        Logger.debug(f"New value: {val!r}")

    return val


def interpolate(box, orig=None, rel=None):
    """
    Interpolates flags in a string with values from the config
    """
    if orig is None:
        orig = Box(box, box_dots=True, default_box=True)

    if rel is None:
        rel = orig

    if isinstance(box, BoxList):
        for i, val in enumerate(box):
            if isinstance(val, Box):
                interpolate(val, orig, rel)
            elif isinstance(val, BoxList):
                interpolate(val, orig, rel)
            elif isinstance(val, str):
                box[i] = interp(val, rel, orig)
    else:
        rel = Box(box, box_dots=True, default_box=True)
        for key, val in box.items():
            if isinstance(val, Box):
                interpolate(val, orig)
            elif isinstance(val, BoxList):
                interpolate(val, orig, rel)
            elif isinstance(val, str):
                box[key] = interp(val, rel, orig)
