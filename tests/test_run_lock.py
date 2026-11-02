"""Tests for repo_sync.run_lock."""

import logging
from pathlib import Path

import pytest

from repo_sync.run_lock import run_lock


def test_lock_taken(tmp_path: Path) -> None:
    with run_lock(tmp_path / "run.lock") as locked:
        assert locked


def test_second_run_is_refused(tmp_path: Path) -> None:
    with run_lock(tmp_path / "run.lock") as first:
        with run_lock(tmp_path / "run.lock") as second:
            assert first and not second


def test_lock_released_after_the_run(tmp_path: Path) -> None:
    with run_lock(tmp_path / "run.lock"):
        pass
    with run_lock(tmp_path / "run.lock") as locked:
        assert locked


def test_lock_released_after_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with run_lock(tmp_path / "run.lock"):
            raise RuntimeError("boom")
    with run_lock(tmp_path / "run.lock") as locked:
        assert locked


def test_leftover_lock_file_does_not_block(tmp_path: Path) -> None:
    # A run that crashed leaves the file behind, but the OS lock died with it
    (tmp_path / "run.lock").write_text("")
    with run_lock(tmp_path / "run.lock") as locked:
        assert locked


def test_missing_folder_is_created(tmp_path: Path) -> None:
    with run_lock(tmp_path / "a" / "b" / "run.lock") as locked:
        assert locked


def test_unwritable_location_runs_unlocked(
    tmp_path: Path, logged: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    with run_lock(blocker / "run.lock") as locked:
        assert locked
    [warning] = logged.records
    assert warning.levelno == logging.WARNING and "run.lock" in warning.message
