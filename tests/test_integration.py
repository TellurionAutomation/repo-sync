"""Syncs the sample manifest against its real public remotes.

These tests need network access to github.com, so they only run when the
SYNC_INTEGRATION environment variable is set:

  SYNC_INTEGRATION=1 pytest tests/test_integration.py

The remotes are public test repositories owned by other people; they are only
ever read from.
"""

import csv
import os
from pathlib import Path

import pytest

from git_helpers import commit_manifest, head_commit, run_git
from repo_sync import cli
from repo_sync.cli import CHECK_COMMAND, SYNC_COMMAND, ExitCode
from repo_sync.manifest import read_manifest

SAMPLE_MANIFEST = Path(__file__).parent / "data" / "test-manifest.csv"

# Tags the "test" branch uses instead of the sample file's, so the two branches differ
TEST_BRANCH_TAGS = {
    "Example-Library": "RELEASE_1.1",
    "Example-Fixtures": "lightweight-tag",
    "Method-A": "RELEASE_1.1",
}

pytestmark = pytest.mark.skipif(
    not os.environ.get("SYNC_INTEGRATION"),
    reason="needs network access; set SYNC_INTEGRATION=1 to run",
)


@pytest.fixture(autouse=True)
def no_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "find_sync_blockers", lambda *args, **kwargs: [])


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    """A manifest repo with the sample file on branch "main" and different tags on branch "test".

    Each Windows directory in the sample is moved into tmp_path.
    """
    with open(SAMPLE_MANIFEST, encoding="utf-8", newline="") as file:
        header, *rows = csv.reader(file)
    name, directory, tag = (
        header.index("Repo"),
        header.index("Directory"),
        header.index("Tag"),
    )
    for row in rows:
        row[directory] = str(tmp_path / "local" / row[directory].split("\\")[-1])
    test_rows = [
        [*row[:tag], TEST_BRANCH_TAGS.get(row[name], row[tag]), *row[tag + 1 :]]
        for row in rows
    ]

    repo = tmp_path / "manifest-repo"
    commit_manifest(repo, "test", test_rows)
    return commit_manifest(repo, "main", rows)


def run(manifest: Path, command: str, *args: str) -> int:
    extra = ["--no-pause"] if command == SYNC_COMMAND else []
    return cli.main(
        [
            command,
            *args,
            "--manifest",
            str(manifest),
            "--no-pull",
            "--no-popup",
            "--log-dir",
            str(manifest.parent.parent / "logs"),
            *extra,
        ]
    )


def assert_at_tags(manifest: Path) -> None:
    """Checks every repo in the checked-out manifest is at its tag's commit"""
    for entry in read_manifest(manifest):
        assert head_commit(entry.local_dir) == run_git(
            entry.local_dir, "rev-list", "-n", "1", entry.tag
        ), entry.name


@pytest.mark.parametrize("branch", ["main", "test"])
def test_sync_sample_file(manifest: Path, branch: str) -> None:
    assert run(manifest, SYNC_COMMAND, branch, "ALL") == ExitCode.OK
    assert_at_tags(manifest)


def test_switch_branches_and_check(manifest: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.csv"
    assert run(manifest, SYNC_COMMAND, "test", "ALL") == ExitCode.OK
    # Switching branch moves existing clones to that branch's tags
    assert run(manifest, SYNC_COMMAND, "main", "ALL") == ExitCode.OK
    assert_at_tags(manifest)
    assert (
        run(manifest, CHECK_COMMAND, "main", "ALL", "--output", str(output))
        == ExitCode.OK
    )
    assert (
        run(manifest, CHECK_COMMAND, "test", "ALL", "--output", str(output))
        == ExitCode.REPOS_NOT_OK
    )
    assert "False" in output.read_text()


def test_sync_one_device(manifest: Path) -> None:
    assert run(manifest, SYNC_COMMAND, "main", "DEVICE_C") == ExitCode.OK
    synced = {path.name for path in (manifest.parent.parent / "local").iterdir()}
    assert synced == {
        "example-library",
        "example-toolkit",
        "example-fixtures",
        "method-b",
        "method-d",
        "method-e",
        "method-g",
    }
