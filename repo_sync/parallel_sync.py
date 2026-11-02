"""Syncs many repositories at once.

Each repository is cloned if needed and checked out to its target tag in a
worker thread. Failures are collected per repository instead of stopping the
run, so one bad repo does not block the rest.
"""

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os

import git

from repo_sync.console_output import logger
from repo_sync.git_environment import configure_non_interactive_git
from repo_sync.repo import Repository

DEFAULT_MAX_WORKERS = 4


@dataclass
class SyncResult:
    """
    Outcome of syncing a single repository

    Attributes:
        repo (Repository): The repository that was synced.
        tag (str): Tag the repository was checked out to.
        cloned (bool): True if the repository had to be cloned first.
        error (Exception | None): The exception raised while syncing, or None on success.
    """

    repo: Repository
    tag: str
    cloned: bool = False
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """Returns True if the repository synced without an error"""
        return self.error is None


def _sync_one(repo: Repository, tag: str, destructive: bool) -> SyncResult:
    """Clones the repo if needed and checks out the tag. Runs in a worker thread."""
    result = SyncResult(repo=repo, tag=tag)
    try:
        if not repo.is_git_repo:
            repo.clone_repo()
            result.cloned = True
        else:
            repo.check_origin()
        repo.checkout_tagged_commit(tag, destructive=destructive)
    except Exception as e:  # Reported per repo so the other repos keep syncing
        result.error = e
    return result


def sync_repositories(
    jobs: list[tuple[Repository, str]],
    destructive: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[SyncResult]:
    """Syncs every repository to its tag, several at a time.

    Progress is printed from the calling thread as each repository finishes.
    Pressing Ctrl+C cancels repositories that have not started yet; ones
    already running are allowed to finish.

    Args:
        jobs: Pairs of repository and the tag to check it out to.
        destructive: Passed to the checkout; if True, local changes
            to tracked files are discarded.
        max_workers: Number of repositories synced at the same time.

    Returns:
        One result per job, in the same order as jobs.

    Raises:
        ValueError: If two jobs share a local directory. Syncing both at once
            would have two threads writing to the same repo.
    """
    _check_unique_dirs(jobs)

    # Fail instead of waiting for a prompt nobody can answer, or a dead
    # connection, while several repos are syncing at once
    configure_non_interactive_git()

    results: list[SyncResult | None] = [None] * len(jobs)
    total = len(jobs)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: dict[Future[SyncResult], int] = {
            pool.submit(_sync_one, repo, tag, destructive): index
            for index, (repo, tag) in enumerate(jobs)
        }
        try:
            for done, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                results[futures[future]] = result
                _log_progress(done, total, result)
        except KeyboardInterrupt:
            pool.shutdown(wait=True, cancel_futures=True)
            raise

    return [result for result in results if result is not None]


def _check_unique_dirs(jobs: list[tuple[Repository, str]]) -> None:
    """Raises ValueError if more than one job points at the same local directory"""
    seen: dict[str, str] = {}
    for repo, _ in jobs:
        key = os.path.normcase(str(repo.local_dir.resolve()))
        if key in seen:
            raise ValueError(
                f'"{repo.name}" and "{seen[key]}" both use the directory {repo.local_dir}'
            )
        seen[key] = repo.name


def _log_progress(done: int, total: int, result: SyncResult) -> None:
    """Logs one line for a finished repository.

    The record also carries the outcome as fields (repo, tag, ok, cloned, done,
    total), so tests can check it without depending on the message wording.
    """
    width = len(str(total))
    fields = {
        "repo": result.repo.name,
        "tag": result.tag,
        "ok": result.ok,
        "cloned": result.cloned,
        "done": done,
        "total": total,
    }
    if result.ok:
        action = "cloned, checked out" if result.cloned else "checked out"
        logger.info(
            f"[{done:>{width}}/{total}] OK      '{result.repo.name}': {action} '{result.tag}'",
            extra=fields,
        )
    else:
        logger.error(
            f"[{done:>{width}}/{total}] FAILED  '{result.repo.name}': {short_error(result.error)}",
            extra=fields,
        )


_GIT_MESSAGE_PREFIXES = ("fatal:", "error:")
# Progress and advice lines that never explain a failure
_GIT_NOISE_PREFIXES = ("warning:", "hint:", "cloning into ")


def short_error(error: Exception | None) -> str:
    """Returns a one-line description of error; git errors are reduced to git's own message.

    Git can give the reason and the outcome on separate lines, e.g. on Windows
    "fatal: 'C:/x' does not appear to be a git repository" then "fatal: Could
    not read from remote repository.", or over SSH "Permission denied
    (publickey)." then the same fatal line. Every such line is kept, joined
    into one sentence, so the reason isn't lost.
    """
    if not (isinstance(error, git.GitCommandError) and str(error.stderr).strip()):
        return str(error)

    # GitPython wraps git's output as "\n  stderr: '<output>'"
    output = str(error.stderr).strip().removeprefix("stderr:").strip().strip("'")
    # Hints such as "Please make sure you have the correct access rights" follow a blank line
    block = output.replace("\r\n", "\n").split("\n\n")[0]
    lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip() and not line.strip().lower().startswith(_GIT_NOISE_PREFIXES)
    ]
    if not lines:
        return output.splitlines()[-1].strip()
    is_message = [line.lower().startswith(_GIT_MESSAGE_PREFIXES) for line in lines]
    if any(is_message):
        # Lines before the first fatal/error line (e.g. SSH's reason) are kept;
        # unprefixed lines after it are details such as a list of files
        first = is_message.index(True)
        lines = lines[:first] + [
            line for line, keep in zip(lines[first:], is_message[first:]) if keep
        ]
    parts = [_strip_git_prefix(line) for line in lines]
    # Each part becomes a sentence, except the last, which is left as git wrote it
    sentences = [
        part if part.endswith((".", "!", "?", ":")) else part + "."
        for part in parts[:-1]
    ]
    return " ".join([*sentences, *parts[-1:]])


def _strip_git_prefix(line: str) -> str:
    for prefix in _GIT_MESSAGE_PREFIXES:
        if line.lower().startswith(prefix):
            return line[len(prefix) :].strip()
    return line
