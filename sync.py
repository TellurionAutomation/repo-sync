"""Launcher so the sync can be run as ``py sync.py ...``; see repo_sync/cli.py for usage."""

import sys

from repo_sync.cli import main

if __name__ == "__main__":
    sys.exit(main())
