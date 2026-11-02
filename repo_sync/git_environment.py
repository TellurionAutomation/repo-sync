"""Settings that stop git from waiting forever on input nobody can give, or on a dead connection.

The script runs unattended and syncs several repos at once, so a git command
that prompts for a password or host key, or sits on a stalled connection,
would block a worker indefinitely. Instead these settings make it fail, so the
repo is reported like any other failure.
"""

import os

import git

from repo_sync import config


def configure_non_interactive_git() -> None:
    """Configures git, for this process and the git commands it runs, to fail instead of waiting.

    - Username/password prompts are turned off, including askpass programs
      (e.g. the one VS Code sets up) and Git Credential Manager's login
      window. Credentials that are already stored are still used.
    - HTTPS transfers slower than GIT_STALL_MIN_BYTES_PER_SECOND for
      GIT_STALL_TIMEOUT_SECONDS are aborted.
    - SSH runs in batch mode (no prompts), gives up connecting after
      GIT_CONNECT_TIMEOUT_SECONDS, and drops a connection that stops
      responding for about GIT_STALL_TIMEOUT_SECONDS. Hosts never seen before
      are trusted on first use; a host whose key has changed is rejected.

    The SSH settings are skipped if a custom SSH command is already set up
    (GIT_SSH_COMMAND, GIT_SSH or core.sshCommand), so it is not overridden.
    Values already set in the environment are kept. Safe to call more than once.
    """
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    # GIT_TERMINAL_PROMPT only covers prompts in a terminal; these would still
    # ask for a login in a window, which nobody is there to answer
    for askpass in ("GIT_ASKPASS", "SSH_ASKPASS"):
        os.environ.pop(askpass, None)
    os.environ["GCM_INTERACTIVE"] = "never"
    os.environ.setdefault(
        "GIT_HTTP_LOW_SPEED_LIMIT", str(config.GIT_STALL_MIN_BYTES_PER_SECOND)
    )
    os.environ.setdefault(
        "GIT_HTTP_LOW_SPEED_TIME", str(config.GIT_STALL_TIMEOUT_SECONDS)
    )
    if not _custom_ssh_configured():
        os.environ["GIT_SSH_COMMAND"] = ssh_command()


def ssh_command() -> str:
    """Returns the SSH command git uses, with options that make it fail instead of waiting"""
    keep_alive_interval = 15
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "accept-new",
        "ConnectTimeout": config.GIT_CONNECT_TIMEOUT_SECONDS,
        "ServerAliveInterval": keep_alive_interval,
        "ServerAliveCountMax": max(
            1, config.GIT_STALL_TIMEOUT_SECONDS // keep_alive_interval
        ),
    }
    return "ssh " + " ".join(f"-o {name}={value}" for name, value in options.items())


def _custom_ssh_configured() -> bool:
    """Returns True if the environment or global git config already chooses how git runs SSH"""
    if os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH"):
        return True
    try:
        return bool(git.cmd.Git().config("--get", "core.sshCommand").strip())
    except (
        git.GitCommandError
    ):  # git config exits with an error when the setting is not set
        return False
