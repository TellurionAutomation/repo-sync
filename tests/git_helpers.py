"""Helpers for building real git repos and manifests to test against."""

import csv
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

MANIFEST_HEADER = [
    "Repo",
    "URL",
    "Directory",
    "Tag",
    "Critical",
    "Device1",
    "Device2",
]


def write_manifest(
    path: Path, rows: list[list[str]], header: list[str] = MANIFEST_HEADER
) -> Path:
    """Writes a manifest with header followed by rows"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def commit_manifest(
    repo_dir: Path,
    branch: str,
    rows: list[list[str]],
    filename: str = "manifest.csv",
) -> Path:
    """Commits a manifest with rows to branch of the repo at repo_dir, creating either if needed.

    Branches are created from the repo's first commit so they are independent
    of each other. Returns the path of the manifest.
    """
    if not (repo_dir / ".git").exists():
        repo_dir.mkdir(parents=True, exist_ok=True)
        run_git(repo_dir, "init", "-q", "-b", "base")
        run_git(repo_dir, "commit", "-q", "--allow-empty", "-m", "base")
    if run_git(repo_dir, "branch", "--list", branch):
        run_git(repo_dir, "checkout", "-q", "-f", branch)
    else:
        run_git(repo_dir, "checkout", "-q", "-f", "-b", branch, "base")
    path = write_manifest(repo_dir / filename, rows)
    run_git(repo_dir, "add", filename)
    run_git(repo_dir, "commit", "-q", "--allow-empty", "-m", f"{branch} manifest")
    return path


def run_git(cwd: Path, *args: str) -> str:
    """Runs a git command in cwd and returns its output"""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_remote(path: Path) -> Path:
    """Creates a repo to act as a remote.

    It has two commits: "v1" (lightweight tag) on the first, where f contains
    "one", and "v2" (annotated tag) on the second, where f contains "two".
    """
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    (path / "f").write_text("one\n")
    run_git(path, "add", "f")
    run_git(path, "commit", "-qm", "one")
    run_git(path, "tag", "v1")
    (path / "f").write_text("two\n")
    run_git(path, "commit", "-qam", "two")
    run_git(path, "tag", "-a", "v2", "-m", "v2")
    return path


def tag_commit(repo_path: Path, tag: str) -> str:
    """Returns the commit SHA that tag points to"""
    return run_git(repo_path, "rev-list", "-n", "1", tag)


def head_commit(repo_path: Path) -> str:
    """Returns the commit SHA that HEAD points to"""
    return run_git(repo_path, "rev-parse", "HEAD")


def remove_tree(path: Path) -> None:
    """Deletes a folder, including git's read-only object files, which shutil.rmtree can't remove on Windows"""

    def make_writable_and_retry(function, failed_path, _error) -> None:
        os.chmod(failed_path, stat.S_IWRITE)
        function(failed_path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=make_writable_and_retry)
