import logging
from datetime import timedelta
from typing import List


Logger = logging.getLogger(__name__)


def format_args(args: List[str]) -> str:
    """
    Format a list of arguments into a copy-pastable command for the terminal.

    This function takes a list of command-line arguments and formats them into
    a multi-line string with proper backslash continuation, making it easy to
    copy and paste into a terminal. Options and their values are kept on the
    same line for readability.

    Parameters
    ----------
    args : List[str]
        List of command-line arguments to format. The first element should be
        the command name, followed by any flags and arguments.

    Returns
    -------
    str
        A formatted multi-line command string with backslash continuations,
        suitable for terminal execution.

    Examples
    --------
    >>> format_args(["python", "script.py", "--input", "file.txt", "--verbose"])
    'python script.py \\
      --input file.txt \\
      --verbose'
    """
    i = 2
    cmd = f"{args[0]} {args[1]} \\"
    while i < len(args):
        cmd += f"\n  {args[i]}"
        i += 1
        if args[i-1].startswith("-") and not args[i].startswith("-"):
            cmd += f" {args[i]}"
            i += 1
        cmd += " \\"
    cmd = cmd[:-1]

    return cmd


def log_elapse(func):
    """
    Logs the elapse time of a function
    """
    @wraps(func) # Preserves the original function's metadata
    def wrapper(*args, **kwargs):
        beg = time.perf_counter()
        ret = func(*args, **kwargs)
        end = time.perf_counter()

        elapse = end - beg
        Logger.debug(f"Finished {func.__name__} in {timedelta(seconds=elapse)}")

        return ret

    return wrapper
