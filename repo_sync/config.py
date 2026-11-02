"""General settings for the sync script.

These are the defaults. To change them on one machine, copy
config_local.example.py to config_local.py in this folder and set the values
there. config_local.py is not tracked by git, so local changes don't conflict
with updates.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Number of repositories synced at the same time
MAX_WORKERS = 4

# Seconds to leave the console open after a sync so users can read the result
FINISH_PAUSE_SECONDS = 5

# Network limits that make git fail instead of hanging on a dead or unresponsive
# remote; see git_environment.py
GIT_CONNECT_TIMEOUT_SECONDS = 30
GIT_STALL_TIMEOUT_SECONDS = 60
GIT_STALL_MIN_BYTES_PER_SECOND = 1000


# Default location of the manifest; the branch of its repo to pull is
# passed on the command line. The "manifest" folder in the project
# root is ignored by git, which suits a test run; real installs usually set
# this in config_local.py.
MANIFEST_PATH = PROJECT_ROOT / "manifest" / "manifest.csv"

# Lock file that stops two runs of the tool overlapping; see run_lock.py
LOCK_PATH = PROJECT_ROOT / "repo-sync.lock"


class LoggingConfig:
    """Configuration settings for logging."""

    # "logs" folder in the project root; file names are
    # YYYYMMDD_HHMMSS_sync.log or YYYYMMDD_HHMMSS_check.log
    LOG_DIR = PROJECT_ROOT / "logs"
    CHECK_LOG_FILENAME = "check.log"
    SYNC_LOG_FILENAME = "sync.log"
    ENCODING = "utf-8"


# Applications that hold files open and make checkouts fail, mapped to the
# name users know them by
SYNC_INCOMPATIBLE_APPS = {
    "HxRun.exe": "Hamilton Run Control",
    "HxMetEd.exe": "Hamilton Method Editor",
}

# Applications that may have the manifest open, which stops it being
# updated, mapped to the name users know them by. Matched anywhere in the
# process name, ignoring case, so "EXCEL" matches EXCEL.EXE.
MANIFEST_EDITORS = {
    "EXCEL": "Excel",
}


# Local overrides; see the module docstring
try:
    from repo_sync.config_local import *  # noqa: F401,F403
except ModuleNotFoundError as error:
    # Only a missing config_local is fine; a broken import inside it is not
    if error.name != "repo_sync.config_local":
        raise
