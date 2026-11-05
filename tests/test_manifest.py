"""Tests for repo_sync.manifest."""

from pathlib import Path

import pytest

from git_helpers import commit_manifest, run_git, write_manifest
from repo_sync.manifest import (
    ManifestEntry,
    ManifestError,
    ManifestState,
    checkout_manifest,
    is_valid_url,
    manifest_state,
    read_manifest,
    select_entries,
)

SAMPLE_MANIFEST = Path(__file__).parent / "data" / "test-manifest.csv"


def make_entry(
    name: str = "repo",
    devices: tuple[str, ...] = ("ALL",),
    local_dir: str = "dir",
    tag: str = "v1",
) -> ManifestEntry:
    """Builds an entry without going through a file"""
    return ManifestEntry(
        name=name,
        url="url",
        local_dir=Path(local_dir),
        tag=tag,
        critical=False,
        devices=devices,
        row=2,
    )


# --- is_valid_url -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/team/repo.git",
        "https://github.com/team/repo",
        "http://git.local:8080/repo.git",
        "ssh://git@bitbucket.org/team/repo.git",
        "git://host.example.com/repo.git",
        "file:///srv/git/repo.git",
        "git@bitbucket.org:team/repo.git",
        "user.name@my-host.example.com:path/to/repo.git",
        "host.example.com:repo.git",
        "/srv/git/repo.git",
        r"C:\git\repo.git",
        "//server/share/repo.git",
    ],
)
def test_valid_urls(value: str) -> None:
    assert is_valid_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "git clone https://github.com/team/repo.git",
        "https://github.com/team/my repo.git",
        " https://github.com/team/repo.git",
        "https://",
        "https:/github.com/team/repo.git",
        "ftp://host/repo.git",
        "repo.git",
        "relative/path/repo.git",
        "C:relative",
        "git@bitbucket.org:",
        "@host:repo",
    ],
)
def test_malformed_urls(value: str) -> None:
    assert not is_valid_url(value)


# --- read_manifest --------------------------------------------------------


def test_reads_rows(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            [
                "Alpha",
                " git@example.com:team/repo-a.git ",
                "C:/a",
                "v1",
                "TRUE",
                "All",
                "",
            ],
            [
                "Beta",
                "https://example.com/b.git",
                "C:/b",
                "v2",
                "",
                "device_a",
                "DEVICE_B",
            ],
        ],
    )
    alpha, beta = read_manifest(path)

    assert alpha == ManifestEntry(
        name="Alpha",
        url="git@example.com:team/repo-a.git",
        local_dir=Path("C:/a"),
        tag="v1",
        critical=True,
        devices=("ALL",),
        row=2,
    )
    assert beta.tag == "v2" and beta.critical is False
    assert beta.devices == ("DEVICE_A", "DEVICE_B")
    assert beta.row == 3


def test_columns_found_by_header_name(tmp_path: Path) -> None:
    header = ["Device1", "tag", "Directory", "Critical", "repo", "Url", "Notes"]
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            [
                "DEVICE_A",
                "v3",
                "C:/a",
                "true",
                "Alpha",
                "https://example.com/a.git",
                "ignored",
            ]
        ],
        header=header,
    )
    [entry] = read_manifest(path)
    assert (entry.name, entry.url, entry.local_dir, entry.tag) == (
        "Alpha",
        "https://example.com/a.git",
        Path("C:/a"),
        "v3",
    )
    assert entry.critical and entry.devices == ("DEVICE_A",)


def test_any_number_of_device_columns(tmp_path: Path) -> None:
    header = [
        "Repo",
        "URL",
        "Directory",
        "Tag",
        "Critical",
        "Device1",
        "Device2",
        "Device3",
        "Device4",
    ]
    path = write_manifest(
        tmp_path / "manifest.csv",
        [["A", "https://example.com/repo.git", "dir", "v1", "", "D1", "", "D3", "D4"]],
        header=header,
    )
    assert read_manifest(path)[0].devices == ("D1", "D3", "D4")


def test_only_device_columns_hold_devices(tmp_path: Path) -> None:
    header = [
        "Repo",
        "URL",
        "Directory",
        "Tag",
        "Critical",
        "Device",
        "device 2",
        "Device notes",
        "DeviceOwner",
        "Devices",
        "Device10",
    ]
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            [
                "A",
                "https://example.com/repo.git",
                "dir",
                "v1",
                "",
                "D1",
                "d2",
                "bench by the window",
                "Sam",
                "D9",
                "D10",
            ]
        ],
        header=header,
    )
    assert read_manifest(path)[0].devices == ("D1", "D2", "D10")


def test_short_rows_are_padded(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv", [["A", "https://example.com/repo.git", "dir", "v1"]]
    )
    [entry] = read_manifest(path)
    assert entry.critical is False and entry.devices == ()


def test_blank_tag_is_read(tmp_path: Path) -> None:
    # A blank tag only matters if the row is synced to this device; select_entries reports it
    path = write_manifest(
        tmp_path / "manifest.csv",
        [["A", "https://example.com/repo.git", "dir", "", "", "All"]],
    )
    assert read_manifest(path)[0].tag == ""


def test_blank_rows_skipped(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            ["A", "https://example.com/repo.git", "dir", "v1", "", "All"],
            [],
            ["", "", "", "", "", "", ""],
            ["B", "https://example.com/repo.git", "dir2", "v1", "", "All"],
        ],
    )
    assert [entry.name for entry in read_manifest(path)] == ["A", "B"]


def test_excel_byte_order_mark(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv",
        [["A", "https://example.com/repo.git", "dir", "v1", "", "All"]],
    )
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert read_manifest(path)[0].name == "A"


def test_missing_columns(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv", [], header=["Repo", "Directory"]
    )
    with pytest.raises(ManifestError, match="missing columns: URL, Tag, Critical"):
        read_manifest(path)


def test_old_format_is_rejected(tmp_path: Path) -> None:
    header = [
        "Method",
        "Repository",
        "Directory",
        "Test",
        "Develop",
        "Main",
        "Critical",
        "Device1",
    ]
    path = write_manifest(
        tmp_path / "manifest.csv",
        [["A", "https://example.com/repo.git", "dir", "v1", "v1", "v1", "", "All"]],
        header=header,
    )
    with pytest.raises(ManifestError, match="missing columns: Repo, URL, Tag"):
        read_manifest(path)


def test_malformed_urls_all_reported(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            ["A", "git clone https://host/repo.git", "dir", "v1", "", "All"],
            ["B", "https://host/repo.git", "dir2", "v1", "", "All"],
            ["C", "not a url", "dir3", "v1", "", "All"],
        ],
    )
    with pytest.raises(ManifestError) as info:
        read_manifest(path)
    assert info.value.problems == [
        "Row 2 (A): URL is malformed: git clone https://host/repo.git",
        "Row 4 (C): URL is malformed: not a url",
    ]


def test_blank_required_cells_all_reported(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path / "manifest.csv",
        [
            ["A", "", "dir", "v1", "", "All"],
            ["B", "https://example.com/repo.git", "dir2", "v1", "", "All"],
            ["", "https://example.com/repo.git", "", "v1", "", "All"],
        ],
    )
    with pytest.raises(ManifestError) as info:
        read_manifest(path)
    assert info.value.problems == [
        "Row 2 (A): URL is blank",
        "Row 4 (unnamed): Repo, Directory is blank",
    ]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="Could not read"):
        read_manifest(tmp_path / "missing.csv")


def test_empty_file(tmp_path: Path) -> None:
    (tmp_path / "manifest.csv").write_text("")
    with pytest.raises(ManifestError, match="empty"):
        read_manifest(tmp_path / "manifest.csv")


def test_reads_sample_file() -> None:
    entries = read_manifest(SAMPLE_MANIFEST)
    assert len(entries) == 10
    by_name = {entry.name: entry for entry in entries}
    assert (
        by_name["Example-Library"].url
        == "https://github.com/githubtraining/hellogitworld.git"
    )
    assert by_name["Method-B"].url == "https://github.com/libgit2/TestGitRepository.git"
    assert by_name["Example-Library"].critical and not by_name["Method-A"].critical
    assert by_name["Example-Fixtures"].tag == "commit-tag"
    assert by_name["Method-G"].devices == ("DEVICE_B", "DEVICE_C", "DEVICE_A")
    assert by_name["Method-D"].local_dir == Path(
        r"C:\Program Files (x86)\HAMILTON\Methods\Examples\method-d"
    )


@pytest.mark.parametrize(
    ("device", "count"),
    [("ALL", 10), ("DEVICE_A", 9), ("DEVICE_B", 7), ("DEVICE_C", 7), ("OTHER", 4)],
)
def test_sample_file_device_selection(device: str, count: int) -> None:
    assert len(select_entries(read_manifest(SAMPLE_MANIFEST), device)) == count


# --- ManifestEntry ---------------------------------------------------------


@pytest.mark.parametrize(
    ("devices", "device", "expected"),
    [
        (("ALL",), "DEVICE_A", True),
        (("DEVICE_A", "DEVICE_B"), "DEVICE_B", True),
        (("DEVICE_A", "DEVICE_B"), "device_b", True),
        (("DEVICE_A",), "DEVICE_B", False),
        ((), "DEVICE_B", False),
        (("DEVICE_A",), "ALL", True),
        ((), "all", True),
    ],
)
def test_applies_to(devices: tuple[str, ...], device: str, expected: bool) -> None:
    assert make_entry(devices=devices).applies_to(device) is expected


def test_to_repository() -> None:
    repo = make_entry(name="A", local_dir="some/dir").to_repository()
    assert (repo.name, repo.url, repo.local_dir) == ("A", "url", Path("some/dir"))


# --- select_entries -----------------------------------------------------------


def test_select_filters_by_device() -> None:
    entries = [
        make_entry("all"),
        make_entry("a", ("DEVICE_A",), "d2"),
        make_entry("b", ("DEVICE_B",), "d3"),
    ]
    assert [e.name for e in select_entries(entries, "DEVICE_A")] == ["all", "a"]
    assert [e.name for e in select_entries(entries, "ALL")] == ["all", "a", "b"]


def test_select_blank_tag() -> None:
    entries = [
        make_entry("a", tag=""),
        make_entry("b", ("OTHER",), "d2", tag=""),
        make_entry("c", local_dir="d3"),
    ]
    with pytest.raises(ManifestError) as info:
        select_entries(entries, "DEVICE_A")
    # "b" is not synced to this device, so its blank tag does not matter
    assert info.value.problems == ["Row 2 (a): Tag is blank"]


@pytest.mark.parametrize("second_dir", ["dir", "dir/", "./dir", "other/../dir"])
def test_select_duplicate_directories(second_dir: str) -> None:
    entries = [make_entry("a", local_dir="dir"), make_entry("b", local_dir=second_dir)]
    with pytest.raises(ManifestError, match="both use the directory"):
        select_entries(entries, "ALL")


def test_select_duplicate_directory_on_other_device_allowed() -> None:
    entries = [
        make_entry("a", ("DEVICE_A",), "dir"),
        make_entry("b", ("DEVICE_B",), "dir"),
    ]
    assert [e.name for e in select_entries(entries, "DEVICE_A")] == ["a"]


def test_select_per_device_tags_for_one_repo() -> None:
    # One repo, one folder, a different tag per device: one row per tag
    entries = [
        make_entry("a", ("DEVICE_A", "DEVICE_B"), "dir", tag="v1"),
        make_entry("a", ("DEVICE_C",), "dir", tag="v2"),
    ]
    assert [e.tag for e in select_entries(entries, "DEVICE_A")] == ["v1"]
    assert [e.tag for e in select_entries(entries, "DEVICE_C")] == ["v2"]
    assert select_entries(entries, "DEVICE_D") == []


@pytest.mark.parametrize(
    ("second_devices", "device"),
    [(("ALL",), "DEVICE_A"), (("DEVICE_A",), "DEVICE_A"), (("DEVICE_C",), "ALL")],
    ids=["all-row", "device-on-both-rows", "all-device"],
)
def test_select_per_device_tags_overlap(
    second_devices: tuple[str, ...], device: str
) -> None:
    entries = [
        make_entry("a", ("DEVICE_A",), "dir", tag="v1"),
        make_entry("a", second_devices, "dir", tag="v2"),
    ]
    with pytest.raises(ManifestError, match="both use the directory"):
        select_entries(entries, device)


def test_select_reports_every_problem() -> None:
    entries = [
        make_entry("a", tag=""),
        make_entry("b", local_dir="dir2"),
        make_entry("c", local_dir="dir2"),
    ]
    with pytest.raises(ManifestError) as info:
        select_entries(entries, "ALL")
    assert len(info.value.problems) == 2


# --- checkout_manifest ----------------------------------------------------


@pytest.fixture
def manifest_remote(tmp_path: Path) -> Path:
    """A manifest repo with different tags on branches "main" and "test"; HEAD is left on "test" """
    remote = tmp_path / "manifest-remote"
    commit_manifest(
        remote,
        "main",
        [["A", "https://example.com/repo.git", "dir", "v-main", "", "All"]],
    )
    commit_manifest(
        remote,
        "test",
        [["A", "https://example.com/repo.git", "dir", "v-test", "", "All"]],
    )
    return remote


def clone(remote: Path, tmp_path: Path) -> Path:
    run_git(tmp_path, "clone", "-q", str(remote), "manifest-local")
    return tmp_path / "manifest-local" / "manifest.csv"


@pytest.mark.parametrize("branch", ["main", "test"])
def test_checkout_branch(manifest_remote: Path, tmp_path: Path, branch: str) -> None:
    manifest = clone(manifest_remote, tmp_path)
    checkout_manifest(manifest, branch)
    assert read_manifest(manifest)[0].tag == f"v-{branch}"
    assert run_git(manifest.parent, "rev-parse", "--abbrev-ref", "HEAD") == branch


def test_checkout_any_branch_name(manifest_remote: Path, tmp_path: Path) -> None:
    commit_manifest(
        manifest_remote,
        "lab-3/new-method-trial",
        [["A", "https://example.com/repo.git", "dir", "v-trial", "", "All"]],
    )
    manifest = clone(manifest_remote, tmp_path)
    checkout_manifest(manifest, "lab-3/new-method-trial")
    assert read_manifest(manifest)[0].tag == "v-trial"


def test_checkout_gets_newest_commit_and_discards_local_changes(
    manifest_remote: Path, tmp_path: Path
) -> None:
    manifest = clone(manifest_remote, tmp_path)
    checkout_manifest(manifest, "main")
    commit_manifest(
        manifest_remote,
        "main",
        [["A", "https://example.com/repo.git", "dir", "v-main-2", "", "All"]],
    )
    manifest.write_text("local edit\n")

    checkout_manifest(manifest, "main")

    assert read_manifest(manifest)[0].tag == "v-main-2"


def test_checkout_resets_diverged_local_branch(
    manifest_remote: Path, tmp_path: Path
) -> None:
    manifest = clone(manifest_remote, tmp_path)
    checkout_manifest(manifest, "main")
    write_manifest(
        manifest,
        [["A", "https://example.com/repo.git", "dir", "v-local", "", "All"]],
    )
    run_git(manifest.parent, "commit", "-qam", "local commit")
    commit_manifest(
        manifest_remote,
        "main",
        [["A", "https://example.com/repo.git", "dir", "v-main-2", "", "All"]],
    )

    checkout_manifest(manifest, "main")

    assert read_manifest(manifest)[0].tag == "v-main-2"


def test_no_pull_uses_local_copy_of_branch(
    manifest_remote: Path, tmp_path: Path, remote_calls: list[str]
) -> None:
    manifest = clone(manifest_remote, tmp_path)
    checkout_manifest(manifest, "main")
    commit_manifest(
        manifest_remote,
        "main",
        [["A", "https://example.com/repo.git", "dir", "v-main-2", "", "All"]],
    )
    run_git(manifest.parent, "checkout", "-q", "test")
    manifest.write_text("local edit\n")
    remote_calls.clear()

    checkout_manifest(manifest, "main", pull=False)

    assert remote_calls == []
    assert run_git(manifest.parent, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert read_manifest(manifest)[0].tag == "v-main"


def test_no_pull_works_without_a_remote(tmp_path: Path) -> None:
    manifest = commit_manifest(
        tmp_path / "manifest-repo",
        "main",
        [["A", "https://example.com/repo.git", "dir", "v1", "", "All"]],
    )
    commit_manifest(
        tmp_path / "manifest-repo",
        "test",
        [["A", "https://example.com/repo.git", "dir", "v2", "", "All"]],
    )
    checkout_manifest(manifest, "main", pull=False)
    assert read_manifest(manifest)[0].tag == "v1"


def test_manifest_in_a_subfolder_of_a_repo_is_rejected(
    manifest_remote: Path, tmp_path: Path
) -> None:
    manifest = clone(manifest_remote, tmp_path)
    (tmp_path / "manifest-local" / "subfolder").mkdir()
    run_git(manifest.parent, "checkout", "-q", "test")
    with pytest.raises(ManifestError, match="Could not check out"):
        checkout_manifest(
            tmp_path / "manifest-local" / "subfolder" / "manifest.csv", "main"
        )
    # The repo the subfolder is in was left alone
    assert run_git(manifest.parent, "rev-parse", "--abbrev-ref", "HEAD") == "test"


def test_checkout_not_in_a_repo(tmp_path: Path) -> None:
    with pytest.raises(
        ManifestError, match="Could not check out branch 'main'.*is not a git repo"
    ):
        checkout_manifest(tmp_path / "manifest.csv", "main")


def test_checkout_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(
        ManifestError, match="Could not check out branch 'main'.*does not exist"
    ):
        checkout_manifest(tmp_path / "gone" / "manifest.csv", "main")


@pytest.mark.parametrize("pull", [True, False])
def test_checkout_missing_branch(
    manifest_remote: Path, tmp_path: Path, pull: bool
) -> None:
    manifest = clone(manifest_remote, tmp_path)
    with pytest.raises(
        ManifestError, match="Could not check out branch 'no-such-branch'"
    ):
        checkout_manifest(manifest, "no-such-branch", pull=pull)


def test_checkout_without_origin(manifest_remote: Path, tmp_path: Path) -> None:
    manifest = clone(manifest_remote, tmp_path)
    run_git(manifest.parent, "remote", "remove", "origin")
    with pytest.raises(ManifestError, match="no remote named 'origin'.*--no-pull"):
        checkout_manifest(manifest, "main")
    # Without pulling, the local branch is still usable
    checkout_manifest(manifest, "test", pull=False)
    assert read_manifest(manifest)[0].tag == "v-test"


def test_checkout_unreachable_remote(manifest_remote: Path, tmp_path: Path) -> None:
    manifest = clone(manifest_remote, tmp_path)
    run_git(manifest.parent, "remote", "set-url", "origin", str(tmp_path / "gone"))
    with pytest.raises(ManifestError, match="Could not check out"):
        checkout_manifest(manifest, "main")


# --- manifest_state -----------------------------------------------------------


@pytest.fixture
def committed_manifest(tmp_path: Path) -> Path:
    return commit_manifest(
        tmp_path / "manifest-repo",
        "main",
        [["A", "https://example.com/repo.git", "dir", "v1", "", "All"]],
    )


def test_state_of_committed_manifest(committed_manifest: Path) -> None:
    assert manifest_state(committed_manifest) == ManifestState(
        commit=run_git(committed_manifest.parent, "rev-parse", "HEAD"), dirty=False
    )


def test_state_of_edited_manifest(committed_manifest: Path) -> None:
    committed_manifest.write_text("edited\n")
    assert manifest_state(committed_manifest).dirty is True


def test_state_of_untracked_manifest(committed_manifest: Path) -> None:
    other = committed_manifest.parent / "other.csv"
    other.write_text("not committed\n")
    assert manifest_state(other).dirty is True


def test_state_ignores_other_changes_in_the_repo(committed_manifest: Path) -> None:
    (committed_manifest.parent / "notes.txt").write_text("scratch\n")
    assert manifest_state(committed_manifest).dirty is False


def test_state_of_manifest_in_a_subfolder(committed_manifest: Path) -> None:
    sub = write_manifest(committed_manifest.parent / "sub" / "manifest.csv", [])
    run_git(committed_manifest.parent, "add", "sub")
    run_git(committed_manifest.parent, "commit", "-qm", "sub")
    state = manifest_state(sub)
    assert state.commit == run_git(committed_manifest.parent, "rev-parse", "HEAD")
    assert state.dirty is False


def test_state_outside_a_repo(tmp_path: Path) -> None:
    loose = write_manifest(tmp_path / "loose" / "manifest.csv", [])
    assert manifest_state(loose) == ManifestState(commit=None, dirty=None)
