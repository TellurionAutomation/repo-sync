"""Tests for repo_sync.console_output."""

import logging
from pathlib import Path
import sys
import types

import pytest

from repo_sync.console_output import console, log_to_file, logger, show_popup


class FakeTk:
    """Stands in for tkinter.Tk, recording what is done to the window"""

    def __init__(self, calls: list[tuple]):
        self.calls = calls
        calls.append(("created",))

    def withdraw(self) -> None:
        self.calls.append(("withdraw",))

    def attributes(self, *args: object) -> None:
        self.calls.append(("attributes", *args))

    def destroy(self) -> None:
        self.calls.append(("destroy",))


@pytest.fixture
def fake_tkinter(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Replaces tkinter with a fake; returns the calls made to it"""
    calls: list[tuple] = []
    messagebox = types.SimpleNamespace(
        showerror=lambda title, message, parent: calls.append(
            ("showerror", title, message)
        )
    )
    tkinter = types.SimpleNamespace(Tk=lambda: FakeTk(calls), messagebox=messagebox)
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)
    return calls


# --- show_popup -----------------------------------------------------------------


def test_popup_shown_on_top_and_closed(
    fake_tkinter: list[tuple], logged: pytest.LogCaptureFixture
) -> None:
    show_popup("Sync Failed", "Method-A: tag not found")
    assert fake_tkinter == [
        ("created",),
        ("withdraw",),
        ("attributes", "-topmost", True),
        ("showerror", "Sync Failed", "Method-A: tag not found"),
        ("destroy",),
    ]
    [record] = logged.records
    assert (
        record.levelno == logging.ERROR
        and record.message == "Sync Failed\n\nMethod-A: tag not found"
    )


def test_popup_message_logged_even_if_window_fails(
    monkeypatch: pytest.MonkeyPatch, logged: pytest.LogCaptureFixture
) -> None:
    def no_display() -> None:
        raise RuntimeError("no display name and no $DISPLAY environment variable")

    monkeypatch.setitem(sys.modules, "tkinter", types.SimpleNamespace(Tk=no_display))
    show_popup("Sync Failed", "details")
    assert logged.messages == ["Sync Failed\n\ndetails"]


def test_popup_without_tkinter(
    monkeypatch: pytest.MonkeyPatch, logged: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "tkinter", None)
    show_popup("Sync Failed", "details")
    assert logged.messages == ["Sync Failed\n\ndetails"]


def test_real_tkinter_imports() -> None:
    # The window itself needs a display and someone to close it, so only the import is checked
    tkinter = pytest.importorskip("tkinter")
    from tkinter import messagebox

    assert callable(tkinter.Tk) and callable(messagebox.showerror)


# --- log_to_file ----------------------------------------------------------------


def test_log_copies_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with log_to_file(tmp_path / "logs", "Log.txt") as log_path:
        logger.info("hello")
        logger.warning("second line")
    assert log_path is not None and log_path.parent == tmp_path / "logs"
    assert log_path.name.endswith("_Log.txt")
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("INFO     hello") and lines[1].endswith(
        "WARNING  second line"
    )
    out = capsys.readouterr().out
    assert "hello" in out and "second line" in out


def test_log_file_is_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even when the console uses colors, none of Rich's formatting reaches the file
    monkeypatch.setattr(console, "_force_terminal", True)
    with log_to_file(tmp_path, "Log.txt") as log_path:
        logger.error("[1/2] FAILED  repo: C:/path")
    assert log_path is not None
    text = log_path.read_text(encoding="utf-8")
    assert "\x1b" not in text and text.rstrip().endswith(
        "ERROR    [1/2] FAILED  repo: C:/path"
    )


def test_log_includes_tracebacks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with log_to_file(tmp_path, "Log.txt") as log_path:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("Unexpected error")
    assert log_path is not None
    text = log_path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in text and "RuntimeError: boom" in text
    assert "RuntimeError: boom" in capsys.readouterr().out


def test_log_stops_after_the_block(tmp_path: Path) -> None:
    handlers = list(logger.handlers)
    with pytest.raises(RuntimeError):
        with log_to_file(tmp_path, "Log.txt") as log_path:
            raise RuntimeError("boom")
    assert logger.handlers == handlers
    logger.info("after")
    assert log_path is not None and "after" not in log_path.read_text(encoding="utf-8")


def test_log_dir_cannot_be_created(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    logged: pytest.LogCaptureFixture,
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    with log_to_file(blocker / "logs", "Log.txt") as log_path:
        logger.info("still shown")
    assert log_path is None
    warning = logged.records[0]
    assert warning.levelno == logging.WARNING and "not-a-folder" in warning.message
    assert "still shown" in capsys.readouterr().out
