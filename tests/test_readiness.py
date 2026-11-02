"""Tests for repo_sync.readiness.

Most tests replace psutil with a fake that lists made-up processes, since the
real check depends on what happens to be running on the machine.
"""

from dataclasses import dataclass, field
import logging
import sys
import types

import pytest

from repo_sync.readiness import find_sync_blockers

psutil = pytest.importorskip("psutil")


@dataclass
class FakeFile:
    path: str


@dataclass
class FakeProcess:
    name: str | None
    files: list[str] = field(default_factory=list)
    open_files_error: type[Exception] | None = None

    @property
    def info(self) -> dict[str, str | None]:
        return {"name": self.name}

    def open_files(self) -> list[FakeFile]:
        if self.open_files_error is not None:
            raise self.open_files_error(pid=1)
        return [FakeFile(path) for path in self.files]


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> list[FakeProcess]:
    """Processes the fake psutil reports as running; tests add to the list"""
    running: list[FakeProcess] = []
    fake = types.SimpleNamespace(
        process_iter=lambda attrs: iter(running),
        NoSuchProcess=psutil.NoSuchProcess,
        AccessDenied=psutil.AccessDenied,
        ZombieProcess=psutil.ZombieProcess,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return running


MANIFEST = (
    r"C:\Program Files (x86)\HAMILTON\manifest\manifest.csv"
)


def test_nothing_running(processes: list[FakeProcess]) -> None:
    processes.append(FakeProcess("explorer.exe"))
    assert find_sync_blockers("manifest.csv") == []


def test_hamilton_apps(processes: list[FakeProcess]) -> None:
    processes.extend(
        [FakeProcess("HxRun.exe"), FakeProcess("HxMetEd.exe"), FakeProcess("HxRun.exe")]
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close Hamilton Method Editor",
        "Close Hamilton Run Control",
    ]


def test_hamilton_apps_can_be_skipped(processes: list[FakeProcess]) -> None:
    processes.append(FakeProcess("HxRun.exe"))
    assert find_sync_blockers("manifest.csv", check_hamilton_apps=False) == []


@pytest.mark.parametrize("excel_name", ["EXCEL.EXE", "excel.exe"])
def test_manifest_open_in_excel(
    processes: list[FakeProcess], excel_name: str
) -> None:
    processes.append(
        FakeProcess(
            excel_name, files=[r"C:\Users\lab\Book1.xlsx", MANIFEST.upper()]
        )
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close the manifest (manifest.csv) in Excel"
    ]


def test_manifest_checked_even_when_skipping_hamilton_apps(
    processes: list[FakeProcess],
) -> None:
    processes.extend(
        [FakeProcess("HxRun.exe"), FakeProcess("EXCEL.EXE", files=[MANIFEST])]
    )
    assert find_sync_blockers("manifest.csv", check_hamilton_apps=False) == [
        "Close the manifest (manifest.csv) in Excel"
    ]


@pytest.mark.parametrize(
    "other_file",
    [
        r"C:\Users\lab\Book1.xlsx",
        r"C:\Users\lab\old-manifest.csv",
        r"C:\Users\lab\manifest.csv.bak",
    ],
)
def test_other_files_open_in_excel(
    processes: list[FakeProcess], other_file: str
) -> None:
    processes.append(FakeProcess("EXCEL.EXE", files=[other_file]))
    assert find_sync_blockers("manifest.csv") == []


def test_manifest_path_with_forward_slashes(processes: list[FakeProcess]) -> None:
    processes.append(FakeProcess("EXCEL.EXE", files=["/Users/lab/manifest.csv"]))
    assert find_sync_blockers("manifest.csv") == [
        "Close the manifest (manifest.csv) in Excel"
    ]


def test_manifest_in_second_excel_window(processes: list[FakeProcess]) -> None:
    processes.extend(
        [
            FakeProcess("EXCEL.EXE", files=[]),
            FakeProcess("EXCEL.EXE", files=[MANIFEST]),
        ]
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close the manifest (manifest.csv) in Excel"
    ]


def test_manifest_editors_come_from_config(
    processes: list[FakeProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "repo_sync.readiness.MANIFEST_EDITORS",
        {"notepad": "Notepad", "EXCEL": "Excel"},
    )
    processes.extend(
        [
            FakeProcess("Notepad.exe", files=[MANIFEST]),
            FakeProcess("EXCEL.EXE", files=[MANIFEST]),
        ]
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close the manifest (manifest.csv) in Excel",
        "Close the manifest (manifest.csv) in Notepad",
    ]


def test_excel_not_checked_when_removed_from_config(
    processes: list[FakeProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("repo_sync.readiness.MANIFEST_EDITORS", {})
    processes.append(FakeProcess("EXCEL.EXE", files=[MANIFEST]))
    assert find_sync_blockers("manifest.csv") == []


def test_all_problems_reported_together(processes: list[FakeProcess]) -> None:
    processes.extend(
        [FakeProcess("HxRun.exe"), FakeProcess("EXCEL.EXE", files=[MANIFEST])]
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close Hamilton Run Control",
        "Close the manifest (manifest.csv) in Excel",
    ]


@pytest.mark.parametrize("error", ["NoSuchProcess", "AccessDenied", "ZombieProcess"])
def test_processes_that_cannot_be_inspected_are_skipped(
    processes: list[FakeProcess], error: str
) -> None:
    processes.extend(
        [
            FakeProcess("EXCEL.EXE", open_files_error=getattr(psutil, error)),
            FakeProcess("EXCEL.EXE", files=[MANIFEST]),
        ]
    )
    assert find_sync_blockers("manifest.csv") == [
        "Close the manifest (manifest.csv) in Excel"
    ]


def test_process_without_a_name(processes: list[FakeProcess]) -> None:
    processes.append(FakeProcess(None))
    assert find_sync_blockers("manifest.csv") == []


def test_psutil_missing(
    monkeypatch: pytest.MonkeyPatch, logged: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert find_sync_blockers("manifest.csv") == []
    [warning] = logged.records
    assert warning.levelno == logging.WARNING and "psutil" in warning.message


def test_real_psutil_runs() -> None:
    # Exercises the real psutil calls on this machine, whatever is running
    problems = find_sync_blockers("a-file-that-is-surely-not-open-anywhere.csv")
    assert all(problem.startswith("Close ") for problem in problems)
