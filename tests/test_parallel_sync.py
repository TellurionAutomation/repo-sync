"""Tests for parallel_sync."""

import logging
import os
from pathlib import Path
import threading
import time

import git
import pytest

from repo_sync import parallel_sync
from git_helpers import head_commit, make_remote, tag_commit
from repo_sync.repo import Repository
from repo_sync.parallel_sync import SyncResult, sync_repositories


@pytest.fixture
def remotes(tmp_path: Path) -> list[Path]:
    """Several remote repos, each with tags v1 and v2"""
    return [make_remote(tmp_path / "remotes" / f"r{i}") for i in range(6)]


def make_jobs(
    tmp_path: Path, remotes: list[Path], tag: str = "v1"
) -> list[tuple[Repository, str]]:
    """One job per remote, each cloning into its own folder under tmp_path/local"""
    return [
        (Repository(remote.name, str(remote), tmp_path / "local" / remote.name), tag)
        for remote in remotes
    ]


# --- basic syncing ------------------------------------------------------------


def test_empty_job_list() -> None:
    assert sync_repositories([]) == []


def test_clones_missing_and_reuses_existing(
    tmp_path: Path, remotes: list[Path]
) -> None:
    jobs = make_jobs(tmp_path, remotes)
    # Clone half of them up front
    for repo, _ in jobs[:3]:
        Repository(repo.name, repo.url, repo.local_dir).clone_repo()

    results = sync_repositories(jobs)

    assert all(result.ok for result in results)
    assert [result.cloned for result in results] == [False] * 3 + [True] * 3
    for remote, (repo, tag) in zip(remotes, jobs):
        assert head_commit(repo.local_dir) == tag_commit(remote, tag)


def test_each_repo_gets_its_own_tag(tmp_path: Path, remotes: list[Path]) -> None:
    jobs = [
        (repo, "v1" if i % 2 else "v2")
        for i, (repo, _) in enumerate(make_jobs(tmp_path, remotes))
    ]
    sync_repositories(jobs)
    for remote, (repo, tag) in zip(remotes, jobs):
        assert head_commit(repo.local_dir) == tag_commit(remote, tag)


def test_results_keep_job_order(tmp_path: Path, remotes: list[Path]) -> None:
    jobs = make_jobs(tmp_path, remotes)
    results = sync_repositories(jobs)
    assert [result.repo for result in results] == [repo for repo, _ in jobs]
    assert [result.tag for result in results] == [tag for _, tag in jobs]


def test_destructive_is_passed_through(tmp_path: Path, remotes: list[Path]) -> None:
    jobs = make_jobs(tmp_path, remotes[:1])
    repo, _ = jobs[0]
    repo.clone_repo()
    (repo.local_dir / "f").write_text("local edit\n")

    [kept] = sync_repositories(jobs)
    assert isinstance(kept.error, git.GitCommandError)
    assert (repo.local_dir / "f").read_text() == "local edit\n"

    [discarded] = sync_repositories(jobs, destructive=True)
    assert discarded.ok
    assert (repo.local_dir / "f").read_text() == "one\n"


def test_disables_git_credential_prompts() -> None:
    sync_repositories([])
    assert os.environ["GIT_TERMINAL_PROMPT"] == "0"


# --- failures -----------------------------------------------------------------


def test_failures_do_not_stop_other_repos(tmp_path: Path, remotes: list[Path]) -> None:
    jobs = make_jobs(tmp_path, remotes)
    bad_url = Repository(
        "bad-url", str(tmp_path / "does-not-exist"), tmp_path / "local" / "bad-url"
    )
    missing_tag = Repository(
        "missing-tag", str(remotes[0]), tmp_path / "local" / "missing-tag"
    )
    non_empty = Repository(
        "non-empty", str(remotes[0]), tmp_path / "local" / "non-empty"
    )
    non_empty.local_dir.mkdir(parents=True)
    (non_empty.local_dir / "junk.txt").write_text("x")
    jobs[1:1] = [(bad_url, "v1"), (missing_tag, "v9"), (non_empty, "v1")]

    results = {result.repo.name: result for result in sync_repositories(jobs)}

    assert isinstance(results["bad-url"].error, git.GitCommandError)
    assert isinstance(results["missing-tag"].error, ValueError)
    assert isinstance(results["non-empty"].error, git.GitCommandError)
    assert [name for name, result in results.items() if result.ok] == [
        remote.name for remote in remotes
    ]


def test_clone_of_another_remote_is_left_alone(
    tmp_path: Path, remotes: list[Path]
) -> None:
    # The manifest row's URL changed after the folder was cloned
    local_dir = tmp_path / "local" / "moved"
    Repository("moved", str(remotes[0]), local_dir).clone_repo()
    before = head_commit(local_dir)

    [result] = sync_repositories(
        [(Repository("moved", str(remotes[1]), local_dir), "v1")], destructive=True
    )

    assert isinstance(result.error, ValueError) and "is a clone of" in str(result.error)
    assert head_commit(local_dir) == before


def test_unexpected_exceptions_are_captured(
    tmp_path: Path, remotes: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: Repository, tag: str, destructive: bool = False) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(Repository, "checkout_tagged_commit", explode)
    results = sync_repositories(make_jobs(tmp_path, remotes))
    assert all(isinstance(result.error, RuntimeError) for result in results)


@pytest.mark.parametrize(
    "second_dir",
    ["local/r0", "local/../local/r0", "local/r0/"],
)
def test_duplicate_dirs_rejected_before_any_work(
    tmp_path: Path, remotes: list[Path], remote_calls: list[str], second_dir: str
) -> None:
    jobs = make_jobs(tmp_path, remotes)
    jobs.append(
        (Repository("dup", str(remotes[1]), str(tmp_path) + "/" + second_dir), "v1")
    )
    with pytest.raises(ValueError, match='"dup" and "r0"'):
        sync_repositories(jobs)
    assert remote_calls == []
    assert not (tmp_path / "local").exists()


# --- concurrency --------------------------------------------------------------


def track_concurrency(
    monkeypatch: pytest.MonkeyPatch, delay: float = 0.05
) -> dict[str, int]:
    """Replaces _sync_one with a slow stub that records how many run at once"""
    stats = {"running": 0, "peak": 0, "started": 0}
    lock = threading.Lock()

    def slow_sync_one(repo: Repository, tag: str, destructive: bool) -> SyncResult:
        with lock:
            stats["running"] += 1
            stats["started"] += 1
            stats["peak"] = max(stats["peak"], stats["running"])
        time.sleep(delay)
        with lock:
            stats["running"] -= 1
        return SyncResult(repo=repo, tag=tag)

    monkeypatch.setattr(parallel_sync, "_sync_one", slow_sync_one)
    return stats


def fake_jobs(tmp_path: Path, count: int) -> list[tuple[Repository, str]]:
    """Jobs that are never really synced; only for use with track_concurrency"""
    return [
        (Repository(f"repo{i}", "url", tmp_path / f"repo{i}"), "v1")
        for i in range(count)
    ]


def test_default_runs_four_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stats = track_concurrency(monkeypatch)
    sync_repositories(fake_jobs(tmp_path, 12))
    assert parallel_sync.DEFAULT_MAX_WORKERS == 4
    assert stats["peak"] == 4


@pytest.mark.parametrize("max_workers", [1, 2, 8])
def test_max_workers_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_workers: int
) -> None:
    stats = track_concurrency(monkeypatch)
    sync_repositories(fake_jobs(tmp_path, 12), max_workers=max_workers)
    assert stats["peak"] == max_workers


def test_many_repos(tmp_path: Path, remote: Path) -> None:
    # One shared remote keeps setup fast; every job still clones its own copy
    jobs = [
        (Repository(f"r{i:02d}", str(remote), tmp_path / "local" / f"r{i:02d}"), "v2")
        for i in range(50)
    ]
    results = sync_repositories(jobs)
    assert len(results) == 50
    assert all(result.ok and result.cloned for result in results)
    assert all(
        head_commit(repo.local_dir) == tag_commit(remote, "v2") for repo, _ in jobs
    )


def test_ctrl_c_cancels_pending_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stats = track_concurrency(monkeypatch)

    def interrupt(*args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(parallel_sync, "_log_progress", interrupt)
    with pytest.raises(KeyboardInterrupt):
        sync_repositories(fake_jobs(tmp_path, 40))
    # Repos already running finish; the rest of the queue never starts
    assert stats["running"] == 0
    assert stats["started"] < 40


# --- output -------------------------------------------------------------------


def test_progress_has_one_line_per_repo(
    tmp_path: Path, remotes: list[Path], logged: pytest.LogCaptureFixture
) -> None:
    jobs = make_jobs(tmp_path, remotes)
    jobs.append(
        (
            Repository(
                "bad-url", str(tmp_path / "does-not-exist"), tmp_path / "local" / "bad"
            ),
            "v1",
        )
    )
    sync_repositories(jobs)

    records = logged.records
    assert len(records) == len(jobs)
    assert [(record.done, record.total) for record in records] == [
        (n, 7) for n in range(1, 8)
    ]
    [failure] = [record for record in records if not record.ok]
    assert failure.levelno == logging.ERROR
    # Git words this error differently on each platform, so only check a reason is given
    assert failure.repo == "bad-url" and failure.message.split("'bad-url': ")[1].strip()
    successes = [record for record in records if record.ok]
    assert sorted(record.repo for record in successes) == [r.name for r in remotes]
    assert all(
        record.levelno == logging.INFO and record.cloned and record.tag == "v1"
        for record in successes
    )


def test_progress_lines_stay_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logged: pytest.LogCaptureFixture
) -> None:
    # The counter is padded so repo names line up from [ 1/12] to [12/12]
    track_concurrency(monkeypatch, delay=0)
    sync_repositories(fake_jobs(tmp_path, 12))
    first, last = logged.records[0], logged.records[-1]
    assert first.message.index(first.repo) == last.message.index(last.repo)


def test_progress_shown_on_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    track_concurrency(monkeypatch, delay=0)
    sync_repositories(fake_jobs(tmp_path, 2))
    out = capsys.readouterr().out
    assert "repo0" in out and "repo1" in out


def test_short_error_reduces_git_errors_to_one_line() -> None:
    error = git.GitCommandError(
        ["git", "clone", "url"], 128, stderr="fatal: repository 'url' does not exist\n"
    )
    assert parallel_sync.short_error(error) == "repository 'url' does not exist"


def test_short_error_drops_warning_lines() -> None:
    error = git.GitCommandError(
        ["git", "fetch"],
        128,
        stderr="warning: something\nfatal: could not read from remote\n",
    )
    assert parallel_sync.short_error(error) == "could not read from remote"


def test_short_error_skips_hint_lines_after_fatal() -> None:
    error = git.GitCommandError(
        ["git", "fetch"],
        128,
        stderr="fatal: Could not read from remote repository.\n\n"
        "Please make sure you have the correct access rights\nand the repository exists.\n",
    )
    assert parallel_sync.short_error(error) == "Could not read from remote repository."


def test_short_error_drops_details_after_error_line() -> None:
    error = git.GitCommandError(
        ["git", "checkout"],
        1,
        stderr="error: Your local changes would be overwritten by checkout:\n\ta.txt\nAborting\n",
    )
    assert (
        parallel_sync.short_error(error)
        == "Your local changes would be overwritten by checkout:"
    )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            # Git on Windows, for a remote path that doesn't exist
            "fatal: 'C:\\git\\nope' does not appear to be a git repository\n"
            "fatal: Could not read from remote repository.\n\n"
            "Please make sure you have the correct access rights\nand the repository exists.\n",
            "'C:\\git\\nope' does not appear to be a git repository. "
            "Could not read from remote repository.",
        ),
        (
            # SSH gives the reason on its own line before git's fatal line
            "Cloning into 'C:/clone'...\r\n"
            "git@github.com: Permission denied (publickey).\r\n"
            "fatal: Could not read from remote repository.\r\n\r\n"
            "Please make sure you have the correct access rights\r\n",
            "git@github.com: Permission denied (publickey). "
            "Could not read from remote repository.",
        ),
        (
            "ERROR: Repository not found.\nfatal: Could not read from remote repository.\n",
            "Repository not found. Could not read from remote repository.",
        ),
    ],
    ids=["windows-missing-path", "ssh-permission-denied", "github-not-found"],
)
def test_short_error_keeps_the_reason(stderr: str, expected: str) -> None:
    error = git.GitCommandError(["git", "clone", "url"], 128, stderr=stderr)
    assert parallel_sync.short_error(error) == expected


def test_short_error_falls_back_to_the_last_line() -> None:
    error = git.GitCommandError(["git", "fetch"], 128, stderr="warning: something odd\n")
    assert parallel_sync.short_error(error) == "warning: something odd"


def test_short_error_other_exceptions() -> None:
    assert (
        parallel_sync.short_error(ValueError('Tag "v9" not found'))
        == 'Tag "v9" not found'
    )


def test_sync_result_ok() -> None:
    repo = Repository("test", "url", "dir")
    assert SyncResult(repo=repo, tag="v1").ok
    assert not SyncResult(repo=repo, tag="v1", error=RuntimeError()).ok


def test_short_error_hides_credentials() -> None:
    error = git.GitCommandError(
        ["git", "clone", "https://ghp_tok@github.com/o/r"],
        128,
        stderr="fatal: unable to access 'https://ghp_tok@github.com/o/r/': 403\n",
    )
    assert parallel_sync.short_error(error) == (
        "unable to access 'https://***@github.com/o/r/': 403"
    )
    assert "ghp_tok" not in parallel_sync.short_error(ValueError("https://ghp_tok@h/r"))
