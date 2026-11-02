"""Tests for repo_sync.git_environment."""

import http.server
import os
from pathlib import Path
import shutil
import socket
import threading
import time

import git
import pytest

from repo_sync import config, git_environment
from repo_sync.git_environment import configure_non_interactive_git, ssh_command
from repo_sync.repo import Repository


def test_sets_prompt_and_stall_limits() -> None:
    configure_non_interactive_git()
    assert os.environ["GIT_TERMINAL_PROMPT"] == "0"
    assert os.environ["GCM_INTERACTIVE"] == "never"
    assert os.environ["GIT_HTTP_LOW_SPEED_LIMIT"] == str(
        config.GIT_STALL_MIN_BYTES_PER_SECOND
    )
    assert os.environ["GIT_HTTP_LOW_SPEED_TIME"] == str(
        config.GIT_STALL_TIMEOUT_SECONDS
    )
    assert os.environ["GIT_SSH_COMMAND"] == ssh_command()


def test_ssh_command_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GIT_CONNECT_TIMEOUT_SECONDS", 12)
    monkeypatch.setattr(config, "GIT_STALL_TIMEOUT_SECONDS", 90)
    assert ssh_command() == (
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12"
        " -o ServerAliveInterval=15 -o ServerAliveCountMax=6"
    )


def test_short_stall_timeout_still_sends_a_keep_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "GIT_STALL_TIMEOUT_SECONDS", 5)
    assert "ServerAliveCountMax=1" in ssh_command()


def test_keeps_existing_stall_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_HTTP_LOW_SPEED_LIMIT", "5")
    monkeypatch.setenv("GIT_HTTP_LOW_SPEED_TIME", "7")
    configure_non_interactive_git()
    assert (
        os.environ["GIT_HTTP_LOW_SPEED_LIMIT"],
        os.environ["GIT_HTTP_LOW_SPEED_TIME"],
    ) == ("5", "7")


@pytest.mark.parametrize("variable", ["GIT_SSH_COMMAND", "GIT_SSH"])
def test_keeps_custom_ssh_from_environment(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "my-ssh -i key")
    configure_non_interactive_git()
    assert os.environ[variable] == "my-ssh -i key"
    if variable == "GIT_SSH":
        assert "GIT_SSH_COMMAND" not in os.environ


def test_keeps_custom_ssh_from_git_config() -> None:
    with open(os.environ["GIT_CONFIG_GLOBAL"], "a") as file:
        file.write("[core]\n\tsshCommand = my-ssh -i key\n")
    configure_non_interactive_git()
    assert "GIT_SSH_COMMAND" not in os.environ


def test_can_be_called_twice() -> None:
    configure_non_interactive_git()
    first = dict(os.environ)
    configure_non_interactive_git()
    assert dict(os.environ) == first


def test_custom_ssh_check_works_without_git_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_config(*args: object) -> str:
        raise git.GitCommandError(["git", "config"], 1)

    monkeypatch.setattr(git.cmd.Git, "config", no_config, raising=False)
    assert git_environment._custom_ssh_configured() is False


# --- real remotes that would otherwise hang ------------------------------------


@pytest.fixture
def login_server():
    """A local HTTP server that asks for a username and password on every request"""

    class AskForLogin(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="repo"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AskForLogin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/team/repo.git"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("askpass", ["GIT_ASKPASS", "SSH_ASKPASS"])
def test_removes_askpass_programs(
    monkeypatch: pytest.MonkeyPatch, askpass: str
) -> None:
    monkeypatch.setenv(askpass, "/some/askpass")
    configure_non_interactive_git()
    assert askpass not in os.environ


@pytest.mark.skipif(
    os.name == "nt", reason="the fake askpass program is a shell script"
)
def test_askpass_program_is_not_used(
    tmp_path: Path, login_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stands in for a login window that nobody answers
    askpass = tmp_path / "askpass.sh"
    askpass.write_text("#!/bin/sh\nsleep 30\necho secret\n")
    askpass.chmod(0o755)
    monkeypatch.setenv("GIT_ASKPASS", str(askpass))
    configure_non_interactive_git()

    start = time.monotonic()
    with pytest.raises(git.GitCommandError) as info:
        Repository("needs-login", login_server, tmp_path / "clone").clone_repo()
    assert time.monotonic() - start < 10
    assert "terminal prompts disabled" in str(info.value.stderr)


def test_login_prompt_fails_instead_of_waiting(
    tmp_path: Path, login_server: str
) -> None:
    configure_non_interactive_git()
    repo = Repository("needs-login", login_server, tmp_path / "clone")
    with pytest.raises(git.GitCommandError) as info:
        repo.clone_repo()
    assert "terminal prompts disabled" in str(info.value.stderr)


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs an ssh client")
def test_unreachable_ssh_host_fails_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "GIT_CONNECT_TIMEOUT_SECONDS", 2)
    configure_non_interactive_git()
    # A non-routable address: connecting never succeeds or fails on its own
    repo = Repository(
        "unreachable", "ssh://git@10.255.255.1/team/repo.git", tmp_path / "clone"
    )
    start = time.monotonic()
    with pytest.raises(git.GitCommandError):
        repo.clone_repo()
    assert time.monotonic() - start < 15


@pytest.fixture
def silent_server():
    """A local server that accepts connections and then never sends anything"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    connections: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                connections.append(server.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}/team/repo.git"
    stop.set()
    thread.join()
    for connection in connections:
        connection.close()
    server.close()


def test_stalled_http_transfer_is_aborted(
    tmp_path: Path, silent_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "GIT_STALL_TIMEOUT_SECONDS", 2)
    configure_non_interactive_git()
    repo = Repository("stalled", silent_server, tmp_path / "clone")
    start = time.monotonic()
    with pytest.raises(git.GitCommandError):
        repo.clone_repo()
    assert time.monotonic() - start < 15
