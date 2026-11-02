"""Checks for open applications and files that would make a sync fail."""

from pathlib import PureWindowsPath

from repo_sync.config import MANIFEST_EDITORS, SYNC_INCOMPATIBLE_APPS
from repo_sync.console_output import logger


def find_sync_blockers(
    manifest_name: str, check_hamilton_apps: bool = True
) -> list[str]:
    """Lists problems that need fixing before syncing.

    Looks for the applications in SYNC_INCOMPATIBLE_APPS, which hold method
    files open, and for the applications in MANIFEST_EDITORS holding the
    manifest open. Processes that cannot be inspected (e.g. due to
    permissions) are skipped.

    Args:
        manifest_name: File name of the manifest, e.g. "manifest.csv".
        check_hamilton_apps: Set to False to skip checking for Hamilton
            applications, e.g. for the check command, which does not change any repos.

    Returns:
        A description of each problem; empty if the sync can go ahead. Also
        empty if psutil is not installed, after logging a warning.
    """
    try:
        import psutil
    except ImportError:
        logger.warning(
            "psutil is not installed, skipping the check for open applications"
        )
        return []

    open_apps: set[str] = set()
    editors_with_manifest: set[str] = set()

    for process in psutil.process_iter(["name"]):
        name = process.info["name"] or ""
        if check_hamilton_apps and name in SYNC_INCOMPATIBLE_APPS:
            open_apps.add(SYNC_INCOMPATIBLE_APPS[name])

        editor = _manifest_editor(name)
        if editor is not None and editor not in editors_with_manifest:
            try:
                open_files = process.open_files() or []
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            # PureWindowsPath understands both \ and / separators
            if any(
                PureWindowsPath(file.path).name.lower() == manifest_name.lower()
                for file in open_files
            ):
                editors_with_manifest.add(editor)

    problems = [f"Close {app}" for app in sorted(open_apps)]
    problems += [
        f"Close the manifest ({manifest_name}) in {editor}"
        for editor in sorted(editors_with_manifest)
    ]
    return problems


def _manifest_editor(process_name: str) -> str | None:
    """Returns the name users know process_name by if it is in MANIFEST_EDITORS, otherwise None"""
    for match, editor in MANIFEST_EDITORS.items():
        if match.upper() in process_name.upper():
            return editor
    return None
