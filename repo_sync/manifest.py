"""Reading the manifest CSV that lists which repos to sync and to which tags.

The manifest lives in its own git repo, with one branch per set of tags
(e.g. one branch per environment); checkout_manifest picks the branch.

Columns are found by header name, so they can be reordered in the file. The
expected headers are Repo, URL, Directory, Tag and Critical,
followed by any number of Device columns (Device1, Device2, ...). A device
column is named "Device" plus an optional number; other headers such as
"Device notes" are ignored like any other extra column.
"""

import csv
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from urllib.parse import urlsplit

import git

from repo_sync.repo import Repository, redact_urls

ALL_DEVICES = "ALL"
_REQUIRED_COLUMNS = ["Repo", "URL", "Directory", "Tag", "Critical"]
# "Device", "Device1", "Device 12"... matched against the upper-cased header
_DEVICE_COLUMN = re.compile(r"^DEVICE\s*\d*$")
_URL_SCHEMES = {"https", "http", "ssh", "git", "file"}
# scp-like form, e.g. git@bitbucket.org:team/repo.git
_SCP_LIKE_URL = re.compile(r"^(?:[\w.-]+@)?[\w.-]+:(?!//)\S+$")


class ManifestError(Exception):
    """The manifest is missing, unreadable or has invalid rows.

    Attributes:
        problems (list[str]): Every problem found, so they can all be fixed at once.
    """

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ManifestEntry:
    """
    One row of the manifest

    Attributes:
        name (str): Human-readable name of the repository.
        url (str): URL of the remote repo.
        local_dir (Path): Where the repo is checked out locally.
        tag (str): Tag the repo is synced to; may be blank in the file, which select_entries rejects.
        critical (bool): Critical repos are always checked by check.
        devices (tuple[str, ...]): Upper-case device IDs this repo is synced to; may include "ALL".
        row (int): Line number in the manifest, for error messages.
    """

    name: str
    url: str
    local_dir: Path
    tag: str
    critical: bool
    devices: tuple[str, ...]
    row: int

    def applies_to(self, device: str) -> bool:
        """Returns True if this repo should be synced to device ("ALL" matches every repo)"""
        device = device.upper()
        return (
            device == ALL_DEVICES
            or ALL_DEVICES in self.devices
            or device in self.devices
        )

    def to_repository(self) -> Repository:
        """Creates a Repository for this entry"""
        return Repository(self.name, self.url, self.local_dir)


def is_valid_url(value: str) -> bool:
    """Returns True if value is a remote URL git accepts.

    Accepts scheme URLs (https://, http://, ssh://, git://, file://), the
    scp-like form (user@host:path) and absolute local paths.
    """
    if not value or any(character.isspace() for character in value):
        return False
    parsed = urlsplit(value)
    if parsed.scheme in _URL_SCHEMES:
        return bool(parsed.netloc) or (parsed.scheme == "file" and bool(parsed.path))
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        return True
    # A drive letter such as "C:dir" also matches the scp-like form, but is a relative path
    return bool(_SCP_LIKE_URL.match(value)) and not PureWindowsPath(value).drive


def read_manifest(path: str | Path) -> list[ManifestEntry]:
    """Reads every row of the manifest.

    Blank rows (e.g. ones Excel leaves at the end) are skipped.

    Args:
        path: Path to the manifest.

    Returns:
        One entry per row, in file order.

    Raises:
        ManifestError: If the file cannot be read, required columns are
            missing, or any row is missing its Repo, URL or Directory or has
            a malformed URL.
    """
    try:
        # utf-8-sig drops the byte order mark Excel adds when saving as UTF-8
        with open(path, encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
    except OSError as e:
        raise ManifestError([f"Could not read manifest {path}: {e}"]) from e

    if not rows:
        raise ManifestError([f"Manifest {path} is empty"])

    header = [column.strip() for column in rows[0]]
    columns = {name.upper(): index for index, name in enumerate(header)}
    missing = [name for name in _REQUIRED_COLUMNS if name.upper() not in columns]
    if missing:
        raise ManifestError(
            [f"Manifest {path} is missing columns: {', '.join(missing)}"]
        )
    device_columns = [
        index
        for index, name in enumerate(header)
        if _DEVICE_COLUMN.match(name.upper())
    ]

    entries: list[ManifestEntry] = []
    problems: list[str] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(index: int) -> str:
            return row[index].strip() if index < len(row) else ""

        def column(name: str) -> str:
            return cell(columns[name.upper()])

        name = column("Repo")
        url = column("URL")
        directory = column("Directory")
        blank = [
            label
            for label, value in (("Repo", name), ("URL", url), ("Directory", directory))
            if not value
        ]
        if blank:
            problems.append(
                f"Row {line_number} ({name or 'unnamed'}): {', '.join(blank)} is blank"
            )
            continue
        if not is_valid_url(url):
            problems.append(f"Row {line_number} ({name}): URL is malformed: {redact_urls(url)}")
            continue

        entries.append(
            ManifestEntry(
                name=name,
                url=url,
                local_dir=Path(directory),
                tag=column("Tag"),
                critical=column("Critical").upper() == "TRUE",
                devices=tuple(
                    cell(index).upper() for index in device_columns if cell(index)
                ),
                row=line_number,
            )
        )

    if problems:
        raise ManifestError(problems)
    return entries


def select_entries(
    entries: list[ManifestEntry], device: str
) -> list[ManifestEntry]:
    """Returns the entries to sync to device, checking they can be synced.

    Args:
        entries: Entries from read_manifest.
        device: Device ID to sync, or "ALL" for every entry.

    Returns:
        The entries for device, in file order.

    Raises:
        ManifestError: If any selected entry has no tag, or two selected
            entries share a local directory.
    """
    selected = [entry for entry in entries if entry.applies_to(device)]
    problems = [
        f"Row {entry.row} ({entry.name}): Tag is blank"
        for entry in selected
        if not entry.tag
    ]

    seen: dict[str, ManifestEntry] = {}
    for entry in selected:
        key = os.path.normcase(os.path.normpath(entry.local_dir))
        if key in seen:
            first = seen[key]
            problems.append(
                f"Rows {first.row} ({first.name}) and {entry.row} ({entry.name}) both use the directory {entry.local_dir}"
            )
        else:
            seen[key] = entry

    if problems:
        raise ManifestError(problems)
    return selected


def checkout_manifest(path: str | Path, branch: str) -> None:
    """Fetches the manifest's repo and checks out the remote's head of branch.

    Any local changes to the manifest repo are discarded, and the local
    branch is reset to the remote's copy of it, so it does not matter whether
    this computer has used the branch before.

    Args:
        path: Path to the manifest, which must be in the top folder of its repo.
            Parent folders are not searched, so a file inside some other repo
            can never cause that repo to be checked out.
        branch: Branch of the manifest repo to check out.

    Raises:
        ManifestError: If the repo or branch cannot be found, or the remote
            cannot be reached.
    """
    folder = Path(path).parent
    try:
        repo = git.Repo(folder)
        if "origin" not in [remote.name for remote in repo.remotes]:
            raise ManifestError(
                [
                    f"Could not check out branch '{branch}' of the manifest repo for {path}: "
                    f"{folder} has no remote named 'origin' to pull from. "
                    "Add one, or run with --local-manifest to read the manifest as it is."
                ]
            )
        repo.remotes.origin.fetch()
        repo.git.checkout("-B", branch, f"origin/{branch}", force=True)
    except (
        git.GitCommandError,
        git.InvalidGitRepositoryError,
        git.NoSuchPathError,
    ) as e:
        # The repo errors stringify to just the folder, so say what was wrong
        if isinstance(e, git.NoSuchPathError):
            reason = f"the folder {folder} does not exist"
        elif isinstance(e, git.InvalidGitRepositoryError):
            reason = (
                f"{folder} is not a git repo (it has no .git folder; "
                "the manifest must be in the top folder of a cloned repo)"
            )
        else:
            reason = str(e)
        raise ManifestError(
            [
                f"Could not check out branch '{branch}' of the manifest repo for {path}: {reason}"
            ]
        ) from e


@dataclass(frozen=True)
class ManifestState:
    """
    Which version of the manifest a run read, for tracing results back to it

    Attributes:
        commit (str | None): Commit the manifest repo's HEAD was at, or None if
            the manifest isn't in a git repo (e.g. a local manifest).
        dirty (bool | None): True if the manifest file differs from that commit
            or isn't committed at all; None if it isn't in a git repo.
    """

    commit: str | None
    dirty: bool | None


def manifest_state(path: str | Path) -> ManifestState:
    """Returns the commit the manifest at path came from and whether it has been changed since.

    Only the manifest file itself counts towards dirty; other changes in its
    repo don't affect what was read. Parent folders are searched, so a local
    manifest that happens to be in a repo is traced too.
    """
    path = Path(path).resolve()
    try:
        repo = git.Repo(path.parent, search_parent_directories=True)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return ManifestState(commit=None, dirty=None)
    if not repo.head.is_valid() or repo.working_tree_dir is None:
        return ManifestState(commit=None, dirty=None)

    relative = path.relative_to(Path(repo.working_tree_dir).resolve()).as_posix()
    dirty = repo.is_dirty(path=relative) or relative in repo.untracked_files
    return ManifestState(commit=repo.head.commit.hexsha, dirty=dirty)
