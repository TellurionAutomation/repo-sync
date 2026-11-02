"""Tests for repo_sync.config."""

from collections.abc import Generator
import importlib
from pathlib import Path
import sys
import types

import pytest

from repo_sync import config

LOCAL_MODULE = "repo_sync.config_local"


@pytest.fixture
def local_module() -> Generator[None, None, None]:
    """Restores config to its real state once a test has swapped config_local"""
    original = sys.modules.get(LOCAL_MODULE)
    try:
        yield
    finally:
        if original is None:
            sys.modules.pop(LOCAL_MODULE, None)
        else:
            sys.modules[LOCAL_MODULE] = original
        importlib.reload(config)


def test_defaults_without_local_settings(local_module: None) -> None:
    # None in sys.modules makes the import fail as if config_local.py were missing
    sys.modules[LOCAL_MODULE] = None  # type: ignore[assignment]
    importlib.reload(config)
    assert config.MANIFEST_PATH == (
        config.PROJECT_ROOT / "manifest" / "manifest.csv"
    )
    assert config.MAX_WORKERS == 4


def test_local_settings_replace_defaults(local_module: None) -> None:
    local = types.ModuleType(LOCAL_MODULE)
    local.MANIFEST_PATH = Path("/elsewhere/manifest.csv")  # type: ignore[attr-defined]
    local.MAX_WORKERS = 9  # type: ignore[attr-defined]
    sys.modules[LOCAL_MODULE] = local
    importlib.reload(config)
    assert config.MANIFEST_PATH == Path("/elsewhere/manifest.csv")
    assert config.MAX_WORKERS == 9
    assert config.FINISH_PAUSE_SECONDS == 5
