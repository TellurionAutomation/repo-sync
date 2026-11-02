"""Logging to the console (with Rich) and to a log file, and showing popups to users.

Every module logs through ``logger``. Messages are shown on the console by a
RichHandler, with colored levels and tracebacks, and inside ``log_to_file``
are also written as plain text to a log file.
"""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from repo_sync.config import LoggingConfig

# Writes to whatever sys.stdout is at the time, so redirected output is still captured
console = Console()

logger = logging.getLogger("repo_sync")

_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(message)s"
_FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _add_console_handler() -> None:
    """Sends the package's log messages to the Rich console"""
    if any(isinstance(handler, RichHandler) for handler in logger.handlers):
        return
    # markup is off so repo names, paths and "[1/7]" counters are shown exactly as they are
    handler = RichHandler(
        console=console,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
        log_time_format="[%X]",
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Handled here only, so messages are not shown twice if the root logger is also configured
    logger.propagate = False


_add_console_handler()


@contextmanager
def log_to_file(log_dir: Path, filename: str) -> Generator[Path | None, None, None]:
    """Copies everything logged inside the with block to a timestamped log file.

    If the log file cannot be created, a warning is logged and messages only
    go to the console.

    Args:
        log_dir: Directory for the log file; created if missing.
        filename: Log file name; the timestamp is added in front of it.

    Yields:
        The path of the log file, or None if it could not be created.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{timestamp}_{filename}"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            log_path, mode="w", encoding=LoggingConfig.ENCODING
        )
    except OSError as e:
        logger.warning("Could not create log file %s: %s", log_path, e)
        yield None
        return

    handler.setFormatter(logging.Formatter(_FILE_FORMAT, _FILE_DATE_FORMAT))
    logger.addHandler(handler)
    try:
        yield log_path
    finally:
        logger.removeHandler(handler)
        handler.close()


def show_popup(title: str, message: str) -> None:
    """Shows a message box and waits for the user to close it.

    Does nothing if a window cannot be shown (e.g. no display, or tkinter
    is not available); the message is always logged as an error as well.
    """
    logger.error("%s\n%s", title, message)
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except (
        Exception
    ):  # A missing popup must never stop the sync from reporting its result
        pass
