"""Shared fixtures.

Tests use small local repos under tmp_path as remotes instead of mocking
GitPython, so they exercise real git behavior without any network access.
"""

from collections.abc import Generator
import os
from pathlib import Path

import git
import pytest

from git_helpers import make_remote
from repo_sync import config
from repo_sync.console_output import console, logger


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops Rich wrapping long log lines, so tests can match whole messages in the console output"""
    monkeypatch.setattr(console, "width", 1000)


@pytest.fixture(autouse=True)
def isolated_lock(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gives each test its own run lock, so tests never touch the project's lock file"""
    monkeypatch.setattr(
        config, "LOCK_PATH", tmp_path_factory.mktemp("lock") / "repo-sync.lock"
    )


@pytest.fixture
def logged(
    caplog: pytest.LogCaptureFixture,
) -> Generator[pytest.LogCaptureFixture, None, None]:
    """Captures the package's log records, which are not passed on to the root logger caplog listens to"""
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def isolated_git(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeps tests independent of the user's git config and of each other.

    The user's global config (commit signing, hooks, default branch...) is
    ignored, commits get a fixed identity, and the variables set by
    configure_non_interactive_git are restored after each test, since it sets
    them for the whole process.
    """
    empty_config = tmp_path_factory.mktemp("gitconfig") / "config"
    empty_config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    for name in (
        "GIT_TERMINAL_PROMPT",
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_HTTP_LOW_SPEED_LIMIT",
        "GIT_HTTP_LOW_SPEED_TIME",
    ):
        monkeypatch.delenv(name, raising=False)
    # Left as they are (e.g. VS Code sets GIT_ASKPASS) so tests see a realistic
    # environment, but registered so they are restored after each test
    for name in ("GIT_ASKPASS", "SSH_ASKPASS", "GCM_INTERACTIVE"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A remote repo with tags v1 and v2"""
    return make_remote(tmp_path / "remote")


@pytest.fixture
def remote_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every git command that talks to a remote (ls-remote, fetch, clone)"""
    calls: list[str] = []
    original_execute = git.cmd.Git.execute

    def recording_execute(self, command, *args, **kwargs):
        for subcommand in ("ls-remote", "fetch", "clone"):
            if subcommand in command:
                calls.append(subcommand)
        return original_execute(self, command, *args, **kwargs)

    monkeypatch.setattr(git.cmd.Git, "execute", recording_execute)
    return calls
