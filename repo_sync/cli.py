"""
Command line entry point for syncing this computer's repos to the manifest.

There are two commands:
  sync   Clones and checks out every repo at its tagged commit, discarding local changes.
  check  Writes a CSV comparing each repo to its tag, without changing any repos.

Usage, e.g.:
  py sync.py sync main DEVICE_B
  py sync.py sync main DEVICE_B --dry-run
  py sync.py check main DEVICE_B --output C:/path/to/results.csv --repo Method-A
  py sync.py sync DEVICE_B --local-manifest
  py sync.py sync DEVICE_B --local-manifest C:/path/to/test-manifest.csv

Both commands first fetch the manifest's repo and check out the head of
BRANCH, discarding any local edits to the manifest, so each branch of the
manifest can list different tags.

--local-manifest skips that: BRANCH is left out, nothing is fetched or
checked out, and the manifest file is read exactly as it is on disk, edits
and all. With no path it reads the usual manifest; with a path it reads that
file, which does not need to be in a repo.

Exit codes (see ExitCode) are the same for both commands.
"""

import argparse
from enum import IntEnum
from pathlib import Path
import sys
import time

from repo_sync import config
from repo_sync import repo_check
from repo_sync.console_output import log_to_file, logger, show_window
from repo_sync.git_environment import configure_non_interactive_git
from repo_sync.manifest import (
    ManifestEntry,
    ManifestError,
    ManifestState,
    checkout_manifest,
    manifest_state,
    read_manifest,
    select_entries,
)
from repo_sync.parallel_sync import SyncResult, short_error, sync_repositories
from repo_sync.readiness import find_sync_blockers
from repo_sync.run_lock import run_lock

SYNC_COMMAND = "sync"
CHECK_COMMAND = "check"

# Shown under the problems when a caller does not give more specific advice
DEFAULT_ADVICE = None


class ExitCode(IntEnum):
    """Exit codes shared by the sync and check commands"""

    OK = 0  # Every repo synced, or every repo checked is clean and at its tag
    REPOS_NOT_OK = 1  # Some repos failed to sync, or are not clean and at their tag
    USAGE_ERROR = 2  # Invalid command line arguments (argparse's own exit code)
    NOT_STARTED = (
        3  # Manifest problem, pull failed, a blocking application is open, or another run is in progress
    )
    INTERNAL_ERROR = 4  # Unexpected error in this script
    INTERRUPTED = 130  # Stopped with Ctrl+C


# Stands in for the path when --local-manifest is given without one
_USUAL_MANIFEST = Path("<usual manifest>")


def build_parser() -> argparse.ArgumentParser:
    """Creates the parser for the sync and check commands"""
    common = argparse.ArgumentParser(add_help=False)
    # Both are optional to argparse so BRANCH can be left out with --local-manifest; parse_args checks them
    common.add_argument(
        "branch",
        metavar="BRANCH",
        nargs="?",
        help="Branch of the manifest's repo listing the tags to use; left out with --local-manifest.",
    )
    common.add_argument(
        "device",
        metavar="DEVICE",
        nargs="?",
        help="Device ID, matched against the device columns; 'ALL' selects every repo.",
    )
    common.add_argument(
        "--manifest",
        type=Path,
        help=f"Path to the manifest (default: {config.MANIFEST_PATH}).",
    )
    common.add_argument(
        "--local-manifest",
        type=Path,
        nargs="?",
        const=_USUAL_MANIFEST,
        metavar="PATH",
        help=(
            "Use the manifest file exactly as it is on disk, including uncommitted edits, "
            "instead of fetching and checking out BRANCH (leave BRANCH out). Nothing is "
            "fetched or reset. With no PATH, reads the usual manifest (--manifest, or "
            f"{config.MANIFEST_PATH}); PATH can be any manifest file, in a repo or not. "
            "Put it after DEVICE, or PATH will be mistaken for DEVICE."
        ),
    )
    common.add_argument(
        "--log-dir",
        type=Path,
        help="Folder for the log file (default: the logs folder in the project root).",
    )
    common.add_argument(
        "--no-popup",
        action="store_true",
        help="Only print problems; do not also show a popup.",
    )

    parser = argparse.ArgumentParser(
        prog="repo-sync",
        description="Syncs repos listed in the manifest.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sync = commands.add_parser(
        SYNC_COMMAND,
        parents=[common],
        help="Clone and check out every repo at its tagged commit, discarding local changes.",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without changing any repos.",
    )
    sync.add_argument(
        "--workers",
        type=int,
        default=config.MAX_WORKERS,
        help=f"Repos synced at the same time (default: {config.MAX_WORKERS}).",
    )
    sync.add_argument(
        "--no-pause",
        action="store_true",
        help="Exit straight away instead of pausing at the end.",
    )

    check = commands.add_parser(
        CHECK_COMMAND,
        parents=[common],
        help="Write a CSV comparing each repo to its tag, without changing any repos.",
    )
    check.add_argument(
        "--output",
        type=Path,
        help="CSV file to write the results to, for Venus; none is written if omitted.",
    )
    check.add_argument(
        "--repo",
        help="Check only this repo plus the critical repos; all repos are checked if omitted or not found.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses and validates the command line arguments"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == SYNC_COMMAND and args.workers < 1:
        parser.error("--workers must be at least 1")

    if args.local_manifest is not None:
        # The only positional given is the device, which argparse fills in as the branch
        if args.device is not None:
            parser.error("BRANCH cannot be used with --local-manifest")
        given_path = args.local_manifest is not _USUAL_MANIFEST
        if args.branch is None and given_path:
            parser.error(
                f"the following arguments are required: DEVICE (if '{args.local_manifest}' "
                "is the device, put --local-manifest after it)"
            )
        if given_path and args.manifest is not None:
            parser.error(
                "give the path to either --manifest or --local-manifest, not both"
            )
        args.device, args.branch = args.branch, None
        if given_path:
            args.manifest = args.local_manifest
        else:
            args.manifest = args.manifest or config.MANIFEST_PATH
            args.local_manifest = args.manifest
    elif args.device is None:
        parser.error("the following arguments are required: BRANCH, DEVICE")
    elif args.manifest is None:
        args.manifest = config.MANIFEST_PATH

    if args.device is None:
        parser.error("the following arguments are required: DEVICE")
    args.device = args.device.upper()
    return args


def main(argv: list[str] | None = None) -> int:
    """Runs the sync or check command and returns its exit code"""
    args = parse_args(argv)

    if args.command == CHECK_COMMAND:
        log_name = config.LoggingConfig.CHECK_LOG_FILENAME
    else:
        log_name = config.LoggingConfig.SYNC_LOG_FILENAME
    log_dir = args.log_dir or config.LoggingConfig.LOG_DIR

    with log_to_file(log_dir, log_name) as log_path:
        # Popups that leave problems out point here for the full list
        args.log_path = log_path
        try:
            with run_lock(config.LOCK_PATH) as locked:
                if not locked:
                    _report_problems(
                        args,
                        "Unable to Sync",
                        ["Another sync or check is already running on this computer."],
                        "Wait for it to finish, then run this again.",
                    )
                    return ExitCode.NOT_STARTED
                return _run(args)
        except KeyboardInterrupt:
            logger.warning("Stopped with Ctrl+C")
            return ExitCode.INTERRUPTED
        except (
            Exception
        ):  # Reported with its own exit code so it is never mistaken for a repo failure
            logger.exception("Unexpected error")
            _report_problems(
                args, "Sync Error", ["The sync script hit an unexpected error."]
            )
            return ExitCode.INTERNAL_ERROR


def _run(args: argparse.Namespace) -> ExitCode:
    """Runs the command chosen in args"""
    # Before anything talks to a remote, including the manifest checkout
    configure_non_interactive_git()
    logger.info(f"Command: {args.command}")
    logger.info(f"Manifest branch: {args.branch or 'none (local manifest)'}")
    logger.info(f"Device: {args.device}")
    logger.info(f"Manifest: {args.manifest}")
    if args.command == SYNC_COMMAND and args.dry_run:
        logger.warning("Dry run: no repos will be changed")

    loaded = _load_entries(args)
    if loaded is None:
        return ExitCode.NOT_STARTED
    entries, state = loaded
    if args.command == CHECK_COMMAND:
        return _run_check(entries, state, args)
    return _run_sync(entries, args)


def _report_problems(
    args: argparse.Namespace,
    title: str,
    problems: list[str],
    advice: str = DEFAULT_ADVICE,
    *,
    more: tuple[str, str] = ("problem", "problems"),
    separator: str = "\n\n",
) -> None:
    """Logs every problem as an error and, unless disabled, shows them in a popup.

    The log always gets the full list. The popup can't scroll, so it shows as
    many whole problems as fit in config.POPUP_MAX_CHARS (see
    _fit_problems) and says what was left out and where to find it.

    Args:
        args: The parsed arguments; uses no_popup and log_path.
        title: Window title, and the first line of the logged message.
        problems: The problems, in the order to show them.
        advice: What the user should do, shown after the problems.
        more: How a count of left-out problems is described, singular and
            plural, e.g. ("repo failed", "repos failed") for
            "...and 3 more repos failed."
        separator: Text between problems; blank lines by default, since
            several long paths run together otherwise.
    """
    logger.error("%s\n\n%s", title, _join_problems(problems, advice, separator))
    if args.no_popup:
        return

    shown, omitted, shortened = _fit_problems(
        problems, config.POPUP_MAX_CHARS, separator
    )
    message = separator.join(shown)
    if omitted or shortened:
        message += "\n\n" + _left_out_note(
            omitted, shortened, more, getattr(args, "log_path", None)
        )
    if advice is not None:
        message += f"\n\n{advice}"
    show_window(title, message)


def _join_problems(problems: list[str], advice: str | None, separator: str) -> str:
    message = separator.join(problems)
    return message if advice is None else f"{message}\n\n{advice}"


def _fit_problems(
    problems: list[str], budget: int, separator: str
) -> tuple[list[str], int, bool]:
    """Picks the problems a popup has room for.

    Whole problems are taken in order while they fit in budget characters,
    counting the separators between them, and stop at the first that doesn't,
    so the popup always shows "the first N". The first problem is always
    shown; if it alone is over budget it is cut at a space and ends in "...".

    Returns:
        The problems to show, how many were left out, and whether the first
        one was shortened.
    """
    if not problems:
        return [], 0, False
    first = problems[0]
    if len(first) > budget:
        cut = first[: max(budget - 3, 1)]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return [cut.rstrip() + "..."], len(problems) - 1, True

    shown = [first]
    used = len(first)
    for problem in problems[1:]:
        used += len(separator) + len(problem)
        if used > budget:
            break
        shown.append(problem)
    return shown, len(problems) - len(shown), False


def _left_out_note(
    omitted: int, shortened: bool, more: tuple[str, str], log_path: Path | None
) -> str:
    """Says what a popup left out and where the full text is.

    Only describes what actually happened: left-out problems, a shortened
    one, or both.
    """
    count = f"...and {omitted} more {more[0] if omitted == 1 else more[1]}"
    if omitted and shortened:
        what = f"{count}, and the one above was shortened. "
        target = "all of them in full"
    elif omitted:
        what = f"{count}. "
        target = "all of them" if omitted > 1 else "that one"
    else:
        what = "The message above was shortened. "
        target = "the full message"
    if log_path is None:
        # log_to_file couldn't create the file, so only the console has it
        return f"{what}The log file could not be created, so {target} can only be found in the console output."
    return f"{what}See the log for {target}:\n{log_path}"


def _load_entries(
    args: argparse.Namespace,
) -> tuple[list[ManifestEntry], ManifestState] | None:
    """Checks the computer is ready, updates and reads the manifest, and selects this device's entries.

    Returns the entries and the version of the manifest they came from, or
    None after reporting the problem if any step fails.
    """
    blockers = find_sync_blockers(
        args.manifest.name, check_hamilton_apps=args.command == SYNC_COMMAND
    )
    if blockers:
        _report_problems(
            args,
            "Unable to Sync",
            blockers,
            "Please close them and run the sync again. If an application is not visible in the task bar, "
            "use Task Manager (ctrl+alt+del) to close hidden instances.",
            more=("application is open", "applications are open"),
        )
        return None

    try:
        if args.local_manifest is not None:
            logger.info(
                f"Reading {args.manifest} as it is, without checking out a branch..."
            )
        else:
            if manifest_state(args.manifest).dirty:
                logger.warning(
                    f"The manifest {args.manifest} has local edits, which are being "
                    "discarded. To sync from the edited file instead, run with "
                    "--local-manifest and no BRANCH."
                )
            logger.info(
                f"Checking out the newest manifest from branch '{args.branch}'..."
            )
            checkout_manifest(args.manifest, args.branch)
        # Logged before reading, so even a run stopped by a bad manifest says which commit it was
        state = manifest_state(args.manifest)
        _log_manifest_state(state)
        entries = select_entries(read_manifest(args.manifest), args.device)
    except ManifestError as e:
        _report_problems(
            args,
            "Manifest Problem",
            e.problems,
            more=("problem in the manifest", "problems in the manifest"),
        )
        return None

    logger.info(f"{len(entries)} repos for device {args.device}")
    return entries, state


def _log_manifest_state(state: ManifestState) -> None:
    """Logs which commit the manifest came from, warning if the file has uncommitted changes"""
    fields = {"manifest_commit": state.commit, "manifest_dirty": state.dirty}
    if state.commit is None:
        logger.info("Manifest commit: none (not in a git repo)", extra=fields)
    elif state.dirty:
        logger.warning(
            f"Manifest commit: {state.commit} (the manifest has uncommitted changes)",
            extra=fields,
        )
    else:
        logger.info(f"Manifest commit: {state.commit}", extra=fields)


def _run_check(
    entries: list[ManifestEntry], state: ManifestState, args: argparse.Namespace
) -> ExitCode:
    """Checks repos without changing them and writes the results to the Venus CSV"""
    if args.repo is not None and args.repo not in {entry.name for entry in entries}:
        logger.warning(f"Repo '{args.repo}' not found, checking all repos")
    to_check = repo_check.entries_to_check(entries, args.repo)
    logger.info(f"Checking {len(to_check)} repos...")
    results = repo_check.check_repositories(to_check)
    if args.output is not None:
        logger.info(f"Writing results to {args.output}...")
        # A local manifest has no branch, so its path is written in the branch's place
        repo_check.write_check_file(
            results, args.output, args.branch or str(args.local_manifest), state
        )

    not_ok = [result for result in results if not result.ok]
    logger.info(
        f"{len(results) - len(not_ok)} of {len(results)} repos are clean and at their tag"
    )
    return ExitCode.REPOS_NOT_OK if not_ok else ExitCode.OK


def _run_sync(entries: list[ManifestEntry], args: argparse.Namespace) -> ExitCode:
    """Syncs every entry and reports any failures"""
    jobs = [(entry.to_repository(), entry.tag) for entry in entries]

    if args.dry_run:
        logger.info("Would sync:")
        for repo, tag in jobs:
            logger.info(
                f"  {repo.name}: {tag} -> {repo.local_dir}",
                extra={"repo": repo.name, "tag": tag},
            )
        return ExitCode.OK

    logger.info(f"Syncing {len(jobs)} repos, {args.workers} at a time...")
    start = time.perf_counter()
    try:
        results = sync_repositories(jobs, destructive=True, max_workers=args.workers)
    except (
        ValueError
    ) as e:  # Duplicate directories; select_entries should already have caught these
        _report_problems(args, "Manifest Problem", [str(e)])
        return ExitCode.NOT_STARTED
    elapsed = time.perf_counter() - start

    failures = [result for result in results if not result.ok]
    logger.info(
        f"Synced {len(results) - len(failures)} of {len(results)} repos in {elapsed:.0f}s",
        extra={"synced": len(results) - len(failures), "total": len(results)},
    )
    if failures:
        _report_problems(
            args,
            "Sync Failed",
            [_describe_failure(result) for result in failures],
            more=("repo failed", "repos failed"),
            # Two blank lines between repos, so each stands apart from the blank line in its own description
            separator="\n\n\n",
        )
        return ExitCode.REPOS_NOT_OK

    logger.info("Finished Sync!")
    if not args.no_pause:
        time.sleep(config.FINISH_PAUSE_SECONDS)
    return ExitCode.OK


def _describe_failure(result: SyncResult) -> str:
    """Describes a failed sync for the summary popup: a marked lead-in line, a blank line, then the error

    The popup is plain text, so an ASCII marker (safe in every console code page) stands in for a bold heading.
    """
    return (
        f">> {result.repo.name} ({result.tag}) encountered an error:\n\n"
        f"{short_error(result.error)}"
    )


if __name__ == "__main__":
    sys.exit(main())
