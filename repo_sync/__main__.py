"""Allows running the sync with ``python -m repo_sync``."""

import sys

from repo_sync.cli import main

sys.exit(main())
