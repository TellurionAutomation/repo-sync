"""Checking local repos against the manifest and writing the results, without changing any repos.

Only the local repos are inspected; the remotes are never contacted.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path

from repo_sync.console_output import logger
from repo_sync.manifest import ManifestEntry, ManifestState
from repo_sync.repo_status import RepositoryStatus

_STATUS_LABELS = {
    RepositoryStatus.CLEAN_MATCH: "OK",
    RepositoryStatus.DIRTY: "DIRTY",
    RepositoryStatus.OUT_OF_DATE: "OUT OF DATE",
    RepositoryStatus.NOT_A_REPO: "NOT A REPO",
}


@dataclass
class CheckResult:
    """
    Result of checking one repository

    Attributes:
        entry (ManifestEntry): The manifest row that was checked.
        tag (str): The tag the repo is expected to be at.
        status (RepositoryStatus): How the local repo compares to tag.
        local_tag (str): ``git describe`` of the local repo, e.g. "v1.2-dirty", or "NONE".
    """

    entry: ManifestEntry
    tag: str
    status: RepositoryStatus
    local_tag: str

    @property
    def ok(self) -> bool:
        """Returns True if the repo is clean and at its tag"""
        return self.status == RepositoryStatus.CLEAN_MATCH


def entries_to_check(
    entries: list[ManifestEntry], active_repo: str | None
) -> list[ManifestEntry]:
    """Returns the critical entries plus active_repo, or every entry if active_repo is not found"""
    if active_repo is None or active_repo not in {entry.name for entry in entries}:
        return list(entries)
    return [entry for entry in entries if entry.critical or entry.name == active_repo]


def check_repositories(entries: list[ManifestEntry]) -> list[CheckResult]:
    """Checks each entry's local repo against its tag, logging one line per repo.

    Args:
        entries: Entries to check.

    Returns:
        One result per entry, in the same order.
    """
    results: list[CheckResult] = []
    width = max((len(label) for label in _STATUS_LABELS.values()), default=0)
    for entry in entries:
        tag = entry.tag
        repo = entry.to_repository()
        result = CheckResult(
            entry=entry,
            tag=tag,
            status=repo.get_status(tag),
            local_tag=repo.describe_head(),
        )
        results.append(result)
        level = logging.INFO if result.ok else logging.WARNING
        logger.log(
            level,
            f"{_STATUS_LABELS[result.status]:<{width}}  {entry.name}: expected {tag}, found {result.local_tag}",
        )
    return results


def write_check_file(
    results: list[CheckResult],
    output_path: str | Path,
    branch: str,
    manifest: ManifestState,
) -> None:
    """Writes the CSV that Venus reads to show users the check results.

    The header row's extra fields are appended after Check Time, so readers
    that find the branch and time by position keep working.

    Args:
        results: Results from check_repositories.
        output_path: Where to write the CSV.
        branch: Manifest branch that was checked against; written to the header row.
        manifest: Commit and dirty state of the manifest; written to the header row.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(output_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            (
                "Repo Name",
                "Directory",
                "Manifest Tag",
                "Local Commit Tag",
                "Expected Tag",
                "Working Tree Clean",
                "",
                "Manifest Branch:",
                branch,
                "",
                "Check Time:",
                timestamp,
                "",
                "Manifest Commit Hash:",
                manifest.commit or "NONE",
                "",
                "Manifest Dirty:",
                "NONE" if manifest.dirty is None else manifest.dirty,
            )
        )
        for result in results:
            writer.writerow(
                (
                    result.entry.name,
                    str(result.entry.local_dir),
                    result.tag,
                    result.local_tag,
                    result.status
                    in (RepositoryStatus.CLEAN_MATCH, RepositoryStatus.DIRTY),
                    not result.local_tag.endswith("-dirty"),
                )
            )
