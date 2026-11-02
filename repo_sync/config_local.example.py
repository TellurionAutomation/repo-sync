"""Local settings for this machine.

Copy this file to config_local.py in the same folder and keep only the
settings you want to change. Anything set here replaces the default of the
same name in config.py. config_local.py is not tracked by git.
"""

from pathlib import Path

# Makes the defaults (and names like PROJECT_ROOT and LoggingConfig) available
# here for reference and editor autocomplete
from repo_sync.config import *  # noqa: F401,F403

# Example: the manifest location used on Hamilton computers
MANIFEST_PATH = Path(
    "C:/Program Files (x86)/HAMILTON/manifest/manifest.csv"
)

# MAX_WORKERS = 8

# Defaults can be extended rather than replaced:
# SYNC_INCOMPATIBLE_APPS["Foo.exe"] = "Foo"
