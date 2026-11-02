"""Stops two syncs or checks running on the same computer at once.

Both commands check out the manifest branch, and sync checks out every repo,
so a scheduled run and a manual one overlapping could interfere. The lock is
an operating system lock on a file, so it is released when the process ends,
even if it crashes; a lock file left on disk does not block later runs.
"""

from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path
from typing import IO

from repo_sync.console_output import logger


def _try_lock(file: IO[str]) -> bool:
    """Takes an exclusive lock on file without waiting; returns False if another run holds it"""
    try:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(file: IO[str]) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def run_lock(path: Path) -> Generator[bool, None, None]:
    """Holds the lock at path for the duration of the with block.

    Yields:
        True if this run holds the lock, False if another run already does.
        If the lock file cannot be created the run goes ahead unlocked, after
        logging a warning, rather than refusing to sync at all.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file = open(path, "a+")
    except OSError as e:
        logger.warning("Could not create lock file %s: %s", path, e)
        yield True
        return

    try:
        if not _try_lock(file):
            yield False
            return
        try:
            yield True
        finally:
            _unlock(file)
    finally:
        file.close()
