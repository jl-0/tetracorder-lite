import logging
import time
from datetime import timedelta
from functools import wraps
from typing import Any, Callable, List, TypeVar


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


F = TypeVar('F', bound=Callable[..., Any])


def log_elapse(func: F) -> F:
    """
    Decorator that logs the elapsed execution time of a function.

    This decorator wraps a function to log debug messages when it starts and
    completes, including the total elapsed time formatted as a timedelta. The
    original function's metadata (name, docstring, etc.) is preserved using
    functools.wraps.

    Parameters
    ----------
    func : callable
        The function to be wrapped and timed.

    Returns
    -------
    callable
        The wrapped function that logs its execution time while preserving
        the original function's signature and return value.

    Examples
    --------
    >>> @log_elapse
    ... def process_data(n):
    ...     time.sleep(1)
    ...     return n * 2
    >>> result = process_data(5)
    DEBUG: Beginning process_data
    DEBUG: Finished process_data in 0:00:01.001234
    >>> result
    10

    Notes
    -----
    The decorator uses ``time.perf_counter()`` for high-resolution timing
    and logs at the DEBUG level, so timing information will only appear
    when debug logging is enabled.
    """
    @wraps(func)  # Preserves the original function's metadata
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        Logger.debug(f"Beginning {func.__name__}")

        beg = time.perf_counter()
        ret = func(*args, **kwargs)
        end = time.perf_counter()

        elapse = end - beg
        Logger.debug(f"Finished {func.__name__} in {timedelta(seconds=elapse)}")

        return ret

    return wrapper  # type: ignore[return-value]
