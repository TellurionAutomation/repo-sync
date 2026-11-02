"""End-to-end tests for the command line (repo_sync.cli), using a real manifest repo and local remotes."""

import csv
import logging
from pathlib import Path
import re

import pytest

from git_helpers import (
    commit_manifest,
    head_commit,
    make_remote,
    remove_tree,
    run_git,
    tag_commit,
    write_manifest,
)
from repo_sync import cli
from repo_sync.cli import CHECK_COMMAND, SYNC_COMMAND, ExitCode
from repo_sync.run_lock import run_lock


@pytest.fixture(autouse=True)
def no_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the open-application check, which looks at the real machine's processes"""
    monkeypatch.setattr(cli, "find_sync_blockers", lambda *args, **kwargs: [])


@pytest.fixture
def popups(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Records popups instead of showing them"""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli, "show_popup", lambda title, message: shown.append((title, message))
    )
    return shown


class Lab:
    """A manifest repo plus the remotes it points at, all under tmp_path.

    rows are committed to the branch being run just before each command, so
    tests can change rows between runs.
    """

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.manifest_repo = tmp_path / "manifest-repo"
        self.manifest = self.manifest_repo / "manifest.csv"
        self.remotes: dict[str, Path] = {}
        self.rows: list[list[str]] = []

    def add(
        self,
        name: str,
        tag: str = "v2",
        critical: bool = False,
        devices: tuple[str, ...] = ("All",),
    ) -> Path:
        """Adds a remote and a manifest row for it; returns the row's local directory"""
        remote = self.remotes.get(name) or make_remote(self.root / "remotes" / name)
        self.remotes[name] = remote
        local_dir = self.local(name)
        padded_devices = list(devices) + [""] * (2 - len(devices))
        self.rows.append(
            [
                name,
                str(remote),
                str(local_dir),
                tag,
                "TRUE" if critical else "",
                *padded_devices,
            ]
        )
        return local_dir

    def local(self, name: str) -> Path:
        return self.root / "local" / name

    @property
    def check_output(self) -> Path:
        """Where check writes its CSV unless a test passes its own --output"""
        return self.root / "check-results.csv"

    def run(self, command: str, branch: str, *args: str) -> int:
        """Commits rows to branch, then runs command with that branch without pulling"""
        commit_manifest(self.manifest_repo, branch, self.rows)
        extra = [
            "--manifest",
            str(self.manifest),
            "--no-pull",
            "--log-dir",
            str(self.root / "logs"),
        ]
        if command == SYNC_COMMAND:
            extra.append("--no-pause")
        elif "--output" not in args:
            extra += ["--output", str(self.check_output)]
        return cli.main([command, branch, *args, *extra])

    def sync(self, branch: str, *args: str) -> int:
        return self.run(SYNC_COMMAND, branch, *args)

    def check(self, branch: str, *args: str) -> int:
        return self.run(CHECK_COMMAND, branch, *args)


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


def with_output(command: str, path: Path) -> list[str]:
    """An --output argument for check, or nothing for sync"""
    return ["--output", str(path)] if command == CHECK_COMMAND else []


# --- exit codes ---------------------------------------------------------------


def test_exit_code_values() -> None:
    assert {code.name: int(code) for code in ExitCode} == {
        "OK": 0,
        "REPOS_NOT_OK": 1,
        "USAGE_ERROR": 2,
        "NOT_STARTED": 3,
        "INTERNAL_ERROR": 4,
        "INTERRUPTED": 130,
    }


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_unexpected_error(
    lab: Lab,
    popups: list,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    def explode(*args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "read_manifest", explode)
    lab.add("alpha")
    assert lab.run(command, "main", "ALL") == ExitCode.INTERNAL_ERROR
    assert "RuntimeError: boom" in capsys.readouterr().out
    assert popups[0][0] == "Sync Error"


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_ctrl_c(lab: Lab, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    def interrupt(*args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "read_manifest", interrupt)
    lab.add("alpha")
    assert lab.run(command, "main", "ALL") == ExitCode.INTERRUPTED


# --- sync -------------------------------------------------------------


def test_sync_clones_and_checks_out_tags(lab: Lab, popups: list) -> None:
    for name in ("alpha", "beta", "gamma"):
        lab.add(name)
    assert lab.sync("main", "ALL") == ExitCode.OK
    for name in ("alpha", "beta", "gamma"):
        assert head_commit(lab.local(name)) == tag_commit(lab.remotes[name], "v2")
    assert popups == []


def test_branches_list_different_tags(lab: Lab) -> None:
    lab.add("alpha", tag="v1")
    commit_manifest(lab.manifest_repo, "test", lab.rows)
    lab.rows[0][3] = "v2"
    commit_manifest(lab.manifest_repo, "main", lab.rows)
    remote = lab.remotes["alpha"]

    def sync_branch(branch: str) -> int:
        return cli.main(
            [
                SYNC_COMMAND,
                branch,
                "ALL",
                "--manifest",
                str(lab.manifest),
                "--no-pull",
                "--no-pause",
                "--log-dir",
                str(lab.root / "logs"),
            ]
        )

    # Each branch of the manifest syncs the repo to its own tag
    assert sync_branch("test") == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(remote, "v1")
    assert sync_branch("main") == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(remote, "v2")
    assert sync_branch("test") == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(remote, "v1")


def test_sync_only_this_devices_repos(lab: Lab) -> None:
    lab.add("everyone", devices=("All",))
    lab.add("mine", devices=("DEVICE_A", "DEVICE_B"))
    lab.add("other", devices=("DEVICE_C",))
    assert lab.sync("main", "device_b") == ExitCode.OK
    assert lab.local("everyone").exists() and lab.local("mine").exists()
    assert not lab.local("other").exists()


def test_per_device_tags_for_one_folder(lab: Lab) -> None:
    local_dir = lab.add("alpha", tag="v1", devices=("DEVICE_A", "DEVICE_B"))
    lab.add("alpha", tag="v2", devices=("DEVICE_C",))
    remote = lab.remotes["alpha"]

    assert lab.sync("main", "DEVICE_A") == ExitCode.OK
    assert head_commit(local_dir) == tag_commit(remote, "v1")
    assert lab.check("main", "DEVICE_A") == ExitCode.OK
    assert lab.check("main", "DEVICE_C") == ExitCode.REPOS_NOT_OK

    # The same folder moves to the other row's tag when synced as DEVICE_C
    assert lab.sync("main", "DEVICE_C") == ExitCode.OK
    assert head_commit(local_dir) == tag_commit(remote, "v2")
    assert lab.check("main", "DEVICE_C") == ExitCode.OK


def test_per_device_tags_refused_for_all(lab: Lab, popups: list) -> None:
    lab.add("alpha", tag="v1", devices=("DEVICE_A",))
    lab.add("alpha", tag="v2", devices=("DEVICE_C",))
    assert lab.sync("main", "ALL") == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "both use the directory" in popups[0][1]


def test_sync_discards_local_changes(lab: Lab) -> None:
    local_dir = lab.add("alpha", tag="v2")
    assert lab.sync("main", "ALL") == ExitCode.OK
    (local_dir / "f").write_text("local edit\n")
    assert lab.sync("main", "ALL") == ExitCode.OK
    assert (local_dir / "f").read_text() == "two\n"


def test_sync_moves_existing_clone_to_new_tag(lab: Lab) -> None:
    lab.add("alpha", tag="v1")
    assert lab.sync("main", "ALL") == ExitCode.OK
    run_git(lab.remotes["alpha"], "tag", "v3", "v2")
    lab.rows[0][3] = "v3"
    assert lab.sync("main", "ALL") == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(lab.remotes["alpha"], "v3")


def test_sync_refuses_clone_of_old_url(lab: Lab, popups: list) -> None:
    lab.add("alpha", tag="v1")
    assert lab.sync("main", "ALL") == ExitCode.OK
    # The repo moved: same folder and tag name, different remote
    lab.rows[0][1] = str(make_remote(lab.root / "remotes" / "alpha-moved"))
    assert lab.sync("main", "ALL") == ExitCode.REPOS_NOT_OK
    assert head_commit(lab.local("alpha")) == tag_commit(lab.remotes["alpha"], "v1")
    assert "alpha (v1):" in popups[0][1] and "is a clone of" in popups[0][1]


def test_sync_failures_reported_together(
    lab: Lab, popups: list, logged: pytest.LogCaptureFixture
) -> None:
    lab.add("good")
    lab.add("missing-tag", tag="v9")
    lab.rows.append(
        [
            "bad-url",
            str(lab.root / "nope"),
            str(lab.local("bad-url")),
            "v1",
            "",
            "All",
            "",
        ]
    )

    assert lab.sync("main", "ALL") == ExitCode.REPOS_NOT_OK

    assert head_commit(lab.local("good")) == tag_commit(lab.remotes["good"], "v2")
    [(title, message)] = popups
    assert title == "Sync Failed"
    assert 'missing-tag (v9): Tag "v9" not found' in message
    # Git words this error differently on each platform, so only check a reason is given
    assert re.search(r"bad-url \(v1\): \S", message)
    assert "good" not in message
    [summary] = [r for r in logged.records if hasattr(r, "synced")]
    assert (summary.synced, summary.total) == (1, 3)


def test_no_popup_flag(
    lab: Lab, popups: list, logged: pytest.LogCaptureFixture
) -> None:
    lab.add("missing-tag", tag="v9")
    assert lab.sync("main", "ALL", "--no-popup") == ExitCode.REPOS_NOT_OK
    assert popups == []
    # The problem report is logged instead, as an error naming the repo
    errors = [r for r in logged.records if r.levelno == logging.ERROR]
    assert any("missing-tag" in r.message and not hasattr(r, "done") for r in errors)


def test_dry_run_changes_nothing(
    lab: Lab, logged: pytest.LogCaptureFixture, remote_calls: list[str]
) -> None:
    lab.add("alpha")
    assert lab.sync("main", "ALL", "--dry-run") == ExitCode.OK
    assert not lab.local("alpha").exists()
    assert remote_calls == []
    [planned] = [r for r in logged.records if getattr(r, "repo", None) == "alpha"]
    assert planned.tag == "v2"


def test_workers_option(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    lab.add("alpha")
    seen: dict[str, int] = {}

    def fake_sync(jobs, destructive, max_workers):
        seen["max_workers"] = max_workers
        return []

    monkeypatch.setattr(cli, "sync_repositories", fake_sync)
    lab.sync("main", "ALL", "--workers", "7")
    assert seen == {"max_workers": 7}


def test_sync_log_file(lab: Lab, logged: pytest.LogCaptureFixture) -> None:
    lab.add("alpha")
    lab.sync("main", "ALL")
    [log] = (lab.root / "logs").iterdir()
    assert log.name.endswith("_sync.log")
    # Every message from the run, including the progress line, is in the file
    content = log.read_text()
    assert logged.messages and all(message in content for message in logged.messages)
    assert any(getattr(r, "repo", None) == "alpha" for r in logged.records)


# --- problems before starting -------------------------------------------------


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_manifest_problems_stop_before_starting(
    lab: Lab, popups: list, command: str
) -> None:
    lab.add("alpha", tag="")
    lab.add("beta")
    assert lab.run(command, "main", "ALL") == ExitCode.NOT_STARTED
    assert not lab.local("beta").exists()
    [(title, message)] = popups
    assert title == "Manifest Problem" and "(alpha): Tag is blank" in message


def test_duplicate_directories_stop_the_sync(lab: Lab, popups: list) -> None:
    lab.add("alpha")
    lab.rows.append(
        [
            "beta",
            "https://example.com/repo.git",
            str(lab.local("alpha")),
            "v1",
            "",
            "All",
            "",
        ]
    )
    assert lab.sync("main", "ALL") == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "both use the directory" in popups[0][1]


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_branch_without_manifest(lab: Lab, popups: list, command: str) -> None:
    commit_manifest(lab.manifest_repo, "main", [], filename="something-else.csv")
    code = cli.main(
        [
            command,
            "main",
            "ALL",
            "--no-pull",
            "--manifest",
            str(lab.manifest),
            "--log-dir",
            str(lab.root / "logs"),
            *with_output(command, lab.check_output),
        ]
    )
    assert code == ExitCode.NOT_STARTED
    assert "Could not read" in popups[0][1]


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_unknown_branch(lab: Lab, popups: list, command: str) -> None:
    lab.add("alpha")
    commit_manifest(lab.manifest_repo, "main", lab.rows)
    code = cli.main(
        [
            command,
            "no-such-branch",
            "ALL",
            "--no-pull",
            "--manifest",
            str(lab.manifest),
            "--log-dir",
            str(lab.root / "logs"),
            *with_output(command, lab.check_output),
        ]
    )
    assert code == ExitCode.NOT_STARTED
    assert "Could not check out branch 'no-such-branch'" in popups[0][1]


def test_open_applications_stop_the_sync(
    lab: Lab, popups: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "find_sync_blockers",
        lambda *args, **kwargs: ["Close Hamilton Run Control"],
    )
    lab.add("alpha")
    assert lab.sync("main", "ALL") == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "Close Hamilton Run Control" in popups[0][1]


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_another_run_in_progress(lab: Lab, popups: list, command: str) -> None:
    lab.add("alpha")
    with run_lock(cli.config.LOCK_PATH):
        assert lab.run(command, "main", "ALL") == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "already running" in popups[0][1]
    # Once the other run finishes, the next one goes ahead
    assert lab.run(command, "main", "ALL") != ExitCode.NOT_STARTED


def test_only_sync_checks_hamilton_apps(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        cli,
        "find_sync_blockers",
        lambda name, check_hamilton_apps: calls.append(check_hamilton_apps) or [],
    )
    lab.add("alpha")
    lab.check("main", "ALL")
    lab.sync("main", "ALL")
    assert calls == [False, True]


# --- pulling the manifest -------------------------------------------------


def clone_manifest_repo(lab: Lab) -> Path:
    """Commits lab's rows to branch "main" of a manifest remote, clones it and returns the clone's manifest"""
    commit_manifest(lab.root / "manifest-remote", "main", lab.rows)
    run_git(lab.root, "clone", "-q", str(lab.root / "manifest-remote"), "manifest-local")
    return lab.root / "manifest-local" / "manifest.csv"


def test_pulls_manifest_before_reading_it(lab: Lab) -> None:
    lab.add("alpha", tag="v1")
    manifest = clone_manifest_repo(lab)
    # The manifest changes on the remote after this computer cloned it
    lab.rows[0][3] = "v2"
    commit_manifest(lab.root / "manifest-remote", "main", lab.rows)

    code = cli.main(
        [
            SYNC_COMMAND,
            "main",
            "ALL",
            "--manifest",
            str(manifest),
            "--no-pause",
            "--log-dir",
            str(lab.root / "logs"),
        ]
    )
    assert code == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(lab.remotes["alpha"], "v2")


def test_dry_run_still_pulls_the_manifest(lab: Lab) -> None:
    lab.add("alpha", tag="v1")
    manifest = clone_manifest_repo(lab)
    lab.rows[0][3] = "v2"
    commit_manifest(lab.root / "manifest-remote", "main", lab.rows)

    code = cli.main(
        [
            SYNC_COMMAND,
            "main",
            "ALL",
            "--dry-run",
            "--manifest",
            str(manifest),
            "--log-dir",
            str(lab.root / "logs"),
        ]
    )
    assert code == ExitCode.OK
    assert ",v2," in manifest.read_text()
    assert not lab.local("alpha").exists()


def test_no_pull_skips_fetching(lab: Lab, remote_calls: list[str]) -> None:
    lab.add("alpha", tag="v1")
    manifest = clone_manifest_repo(lab)
    lab.rows[0][3] = "v2"
    commit_manifest(lab.root / "manifest-remote", "main", lab.rows)
    remote_calls.clear()

    code = cli.main(
        [
            SYNC_COMMAND,
            "main",
            "ALL",
            "--no-pull",
            "--dry-run",
            "--manifest",
            str(manifest),
            "--log-dir",
            str(lab.root / "logs"),
        ]
    )
    assert code == ExitCode.OK
    assert remote_calls == []
    assert ",v1," in manifest.read_text()


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_pull_failure_stops_before_starting(
    lab: Lab, popups: list, command: str
) -> None:
    lab.add("alpha")
    manifest = write_manifest(
        lab.root / "not-a-repo" / "manifest.csv", lab.rows
    )
    code = cli.main(
        [
            command,
            "main",
            "ALL",
            "--manifest",
            str(manifest),
            "--log-dir",
            str(lab.root / "logs"),
            *with_output(command, lab.check_output),
        ]
    )
    assert code == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "Could not check out branch 'main'" in popups[0][1]


def test_checks_out_the_given_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        cli,
        "checkout_manifest",
        lambda path, branch, pull: calls.append((branch, pull)),
    )
    manifest = write_manifest(tmp_path / "manifest.csv", [])
    for extra in ([], ["--no-pull"]):
        cli.main(
            [
                SYNC_COMMAND,
                "any/branch-name",
                "ALL",
                *extra,
                "--manifest",
                str(manifest),
                "--no-pause",
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )
    assert calls == [("any/branch-name", True), ("any/branch-name", False)]


# --- --local-manifest -------------------------------------------------------------


def run_local_manifest(lab: Lab, command: str, *args: str) -> tuple[int, Path]:
    """Writes lab's rows to a manifest outside any repo and runs command on it with --local-manifest"""
    manifest = write_manifest(lab.root / "loose" / "manifest.csv", lab.rows)
    extra = ["--no-pause"] if command == SYNC_COMMAND else []
    code = cli.main(
        [
            command,
            "ALL",
            "--local-manifest",
            str(manifest),
            "--log-dir",
            str(lab.root / "logs"),
            *extra,
            *args,
        ]
    )
    return code, manifest


def test_sync_local_manifest(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_checkout(*args: object, **kwargs: object) -> None:
        raise AssertionError("--local-manifest must not check out a branch")

    monkeypatch.setattr(cli, "checkout_manifest", no_checkout)
    lab.add("alpha", tag="v1")
    code, manifest = run_local_manifest(lab, SYNC_COMMAND)
    assert code == ExitCode.OK
    assert head_commit(lab.local("alpha")) == tag_commit(lab.remotes["alpha"], "v1")
    assert not (manifest.parent / ".git").exists()


def test_check_local_manifest_names_the_file(lab: Lab) -> None:
    lab.add("alpha")
    lab.sync("main", "ALL")
    code, manifest = run_local_manifest(
        lab, CHECK_COMMAND, "--output", str(lab.check_output)
    )
    assert code == ExitCode.OK
    # A local manifest has no branch, so its path goes in the branch slot
    header = read_output(lab.check_output)[0]
    assert header[7:9] == ["Manifest Branch:", str(manifest)]
    # The loose file isn't in a repo, so there's no commit to trace it to
    assert header[13:18] == ["Manifest Commit Hash:", "NONE", "", "Manifest Dirty:", "NONE"]


def test_local_manifest_problems_stop_before_starting(lab: Lab, popups: list) -> None:
    lab.add("alpha", tag="")
    code, _ = run_local_manifest(lab, SYNC_COMMAND)
    assert code == ExitCode.NOT_STARTED
    assert not lab.local("alpha").exists()
    assert "(alpha): Tag is blank" in popups[0][1]


def test_local_manifest_arguments() -> None:
    args = cli.parse_args([SYNC_COMMAND, "device_b", "--local-manifest", "some/file.csv"])
    assert (args.branch, args.device, args.manifest) == (
        None,
        "DEVICE_B",
        Path("some/file.csv"),
    )




def read_output(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Returns the header row and the other rows keyed by repo name"""
    with open(path, encoding="utf-8", newline="") as file:
        header, *rows = csv.reader(file)
    return header, {row[0]: row for row in rows}


def test_check_output_file(lab: Lab, tmp_path: Path, remote_calls: list[str]) -> None:
    for name in ("clean", "dirty", "old", "missing"):
        lab.add(name, critical=True)
    assert lab.sync("main", "ALL") == ExitCode.OK
    (lab.local("dirty") / "f").write_text("local edit\n")
    run_git(lab.local("old"), "checkout", "-q", "v1")
    remove_tree(lab.local("missing"))
    remote_calls.clear()

    output = tmp_path / "out.csv"
    assert lab.check("main", "ALL", "--output", str(output)) == ExitCode.REPOS_NOT_OK
    assert remote_calls == []

    header, rows = read_output(output)
    assert header[:6] == [
        "Repo Name",
        "Directory",
        "Manifest Tag",
        "Local Commit Tag",
        "Expected Tag",
        "Working Tree Clean",
    ]
    assert header[7:9] == ["Manifest Branch:", "main"]
    assert header[10] == "Check Time:"
    assert header[13:18] == [
        "Manifest Commit Hash:",
        head_commit(lab.manifest_repo),
        "",
        "Manifest Dirty:",
        "False",
    ]
    assert rows["clean"][2:] == ["v2", "v2", "True", "True"]
    assert rows["dirty"][2:] == ["v2", "v2-dirty", "True", "False"]
    assert rows["old"][2:] == ["v2", "v1", "False", "True"]
    assert rows["missing"][2:] == ["v2", "NONE", "False", "True"]
    assert rows["clean"][1] == str(lab.local("clean"))


@pytest.mark.parametrize(
    ("break_repo", "expected"),
    [
        (lambda path: None, ExitCode.OK),
        (lambda path: (path / "f").write_text("edit\n"), ExitCode.REPOS_NOT_OK),
        (lambda path: run_git(path, "checkout", "-q", "v1"), ExitCode.REPOS_NOT_OK),
        (lambda path: remove_tree(path), ExitCode.REPOS_NOT_OK),
    ],
    ids=["clean", "dirty", "out-of-date", "missing"],
)
def test_check_exit_code(lab: Lab, break_repo, expected: ExitCode) -> None:
    lab.add("alpha")
    lab.sync("main", "ALL")
    break_repo(lab.local("alpha"))
    assert lab.check("main", "ALL") == expected


def test_check_against_another_branch(lab: Lab) -> None:
    lab.add("alpha", tag="v2")
    lab.sync("main", "ALL")
    lab.rows[0][3] = "v1"
    assert lab.check("test", "ALL") == ExitCode.REPOS_NOT_OK
    assert read_output(lab.check_output)[0][7:9] == ["Manifest Branch:", "test"]


def test_check_repo_plus_critical(lab: Lab) -> None:
    lab.add("critical", critical=True)
    lab.add("chosen")
    lab.add("other")
    lab.check("main", "ALL", "--repo", "chosen")
    assert list(read_output(lab.check_output)[1]) == ["critical", "chosen"]


@pytest.mark.parametrize(
    "repo_args", [[], ["--repo", "no-such-repo"]], ids=["omitted", "not-found"]
)
def test_check_all_repos(lab: Lab, repo_args: list[str]) -> None:
    lab.add("critical", critical=True)
    lab.add("other")
    lab.check("main", "ALL", *repo_args)
    assert list(read_output(lab.check_output)[1]) == ["critical", "other"]


def test_check_changes_nothing_but_writes_output(lab: Lab) -> None:
    lab.add("alpha", critical=True)
    assert lab.check("main", "ALL") == ExitCode.REPOS_NOT_OK
    assert not lab.local("alpha").exists()
    assert list(read_output(lab.check_output)[1]) == ["alpha"]


def test_check_without_output_writes_no_file(lab: Lab) -> None:
    lab.add("alpha")
    lab.sync("main", "ALL")
    commit_manifest(lab.manifest_repo, "main", lab.rows)
    args = [
        "--manifest",
        str(lab.manifest),
        "--no-pull",
        "--log-dir",
        str(lab.root / "logs"),
    ]
    assert cli.main([CHECK_COMMAND, "main", "ALL", *args]) == ExitCode.OK
    assert not lab.check_output.exists()


@pytest.mark.parametrize("command", [SYNC_COMMAND, CHECK_COMMAND])
def test_manifest_commit_is_logged(
    lab: Lab, logged: pytest.LogCaptureFixture, command: str
) -> None:
    lab.add("alpha")
    lab.run(command, "main", "ALL")
    [record] = [r for r in logged.records if hasattr(r, "manifest_commit")]
    assert record.manifest_commit == head_commit(lab.manifest_repo)
    assert record.manifest_dirty is False
    assert record.manifest_commit in "\n".join(
        log.read_text() for log in (lab.root / "logs").iterdir()
    )


def test_edited_local_manifest_is_reported_dirty(
    lab: Lab, logged: pytest.LogCaptureFixture
) -> None:
    # A --local-manifest inside a repo is traced to its commit, and edits to it show
    lab.add("alpha")
    manifest = commit_manifest(lab.manifest_repo, "main", lab.rows)
    lab.add("beta")
    write_manifest(manifest, lab.rows)
    code = cli.main(
        [
            CHECK_COMMAND,
            "ALL",
            "--local-manifest",
            str(manifest),
            "--output",
            str(lab.check_output),
            "--log-dir",
            str(lab.root / "logs"),
        ]
    )
    assert code == ExitCode.REPOS_NOT_OK
    [record] = [r for r in logged.records if hasattr(r, "manifest_commit")]
    assert record.levelno == logging.WARNING and record.manifest_dirty is True
    assert read_output(lab.check_output)[0][16:18] == ["Manifest Dirty:", "True"]


def test_check_log_file(lab: Lab) -> None:
    lab.add("alpha", critical=True)
    lab.check("main", "ALL")
    [log] = (lab.root / "logs").iterdir()
    assert log.name.endswith("_check.log")


# --- arguments ----------------------------------------------------------------


def test_sync_arguments() -> None:
    args = cli.parse_args(
        [SYNC_COMMAND, "release/2.0", "device_b", "--dry-run", "--workers", "2"]
    )
    assert (args.command, args.branch, args.device, args.no_pull) == (
        SYNC_COMMAND,
        "release/2.0",
        "DEVICE_B",
        False,
    )
    assert args.dry_run and args.workers == 2 and not args.no_pause
    assert args.manifest == cli.config.MANIFEST_PATH


def test_check_arguments() -> None:
    args = cli.parse_args(
        [
            CHECK_COMMAND,
            "Main",
            "ALL",
            "--no-pull",
            "--repo",
            "Method-A",
            "--output",
            "out.csv",
        ]
    )
    # Branch names are case-sensitive in git, so they are passed through unchanged
    assert (args.command, args.branch, args.device, args.no_pull) == (
        CHECK_COMMAND,
        "Main",
        "ALL",
        True,
    )
    assert (args.repo, args.output) == ("Method-A", Path("out.csv"))


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["main", "ALL"],
        [SYNC_COMMAND],
        [SYNC_COMMAND, "main"],
        [SYNC_COMMAND, "main", "ALL", "extra"],
        [SYNC_COMMAND, "main", "ALL", "--workers", "0"],
        [SYNC_COMMAND, "main", "ALL", "--output", "out.csv"],
        [SYNC_COMMAND, "main", "ALL", "--branch", "b"],
        [SYNC_COMMAND, "main", "ALL", "--test"],
        [SYNC_COMMAND, "main", "ALL", "-repo-check", "a", "out.csv"],
        [CHECK_COMMAND, "main", "ALL", "--output", "out.csv", "--dry-run"],
        [CHECK_COMMAND, "main", "ALL", "--output", "out.csv", "--workers", "2"],
        [SYNC_COMMAND, "--local-manifest", "x.csv"],
        [SYNC_COMMAND, "main", "ALL", "--local-manifest", "x.csv"],
        [SYNC_COMMAND, "ALL", "--local-manifest", "x.csv", "--manifest", "y.csv"],
        [SYNC_COMMAND, "ALL", "--local-manifest", "x.csv", "--no-pull"],
    ],
)
def test_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.parse_args(argv)
    assert info.value.code == ExitCode.USAGE_ERROR
