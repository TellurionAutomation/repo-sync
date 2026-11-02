"""
Command line entry point for syncing this computer's repos to the manifest.

There are two commands:
  sync   Clones and checks out every repo at its tagged commit, discarding local changes.
  check  Writes a CSV comparing each repo to its tag, without changing any repos.

Usage, e.g.:
  py sync.py sync main DEVICE_B
  py sync.py sync main DEVICE_B --dry-run
  py sync.py check main DEVICE_B --output C:/path/to/results.csv --repo Method-A
  py sync.py sync DEVICE_B --local-manifest C:/path/to/test-manifest.csv

Both commands first check out the head of BRANCH in the manifest's repo,
so each branch of the manifest can list different tags. --no-pull uses
the local copy of the branch without contacting the remote.

--local-manifest reads the given file as it is, e.g. for testing a manifest
that is not in a repo. BRANCH is left out and no checkout is done.

Exit codes (see ExitCode) are the same for both commands.
"""

import argparse
from enum import IntEnum
from pathlib import Path
import sys
import time

from repo_sync import config
from repo_sync import repo_check
from repo_sync.console_output import log_to_file, logger, show_popup
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
        "--no-pull",
        action="store_true",
        help="Use the local copy of BRANCH without fetching it from the remote first.",
    )
    common.add_argument(
        "--manifest",
        type=Path,
        help=f"Path to the manifest (default: {config.MANIFEST_PATH}).",
    )
    common.add_argument(
        "--local-manifest",
        type=Path,
        metavar="PATH",
        help="Read this manifest as it is, without checking out a branch; it does not need to be in a repo.",
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
        for option, used in (
            ("--manifest", args.manifest is not None),
            ("--no-pull", args.no_pull),
        ):
            if used:
                parser.error(f"{option} cannot be used with --local-manifest")
        args.device, args.branch = args.branch, None
        args.manifest = args.local_manifest
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

    with log_to_file(log_dir, log_name):
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
) -> None:
    """Logs problems as an error and, unless disabled, shows them in a popup"""
    message = "\n".join(problems)
    if advice is not None:
        message += f"\n\n{advice}"
    if args.no_popup:
        logger.error("%s\n%s", title, message)
    else:
        show_popup(title, message)


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
        )
        return None

    try:
        if args.local_manifest is not None:
            logger.info(
                "Reading the local manifest without checking out a branch..."
            )
        else:
            if args.no_pull:
                logger.info(
                    f"Checking out the local copy of branch '{args.branch}' without pulling it..."
                )
            else:
                logger.info(
                    f"Checking out the newest manifest from branch '{args.branch}'..."
                )
            checkout_manifest(args.manifest, args.branch, pull=not args.no_pull)
        # Logged before reading, so even a run stopped by a bad manifest says which commit it was
        state = manifest_state(args.manifest)
        _log_manifest_state(state)
        entries = select_entries(read_manifest(args.manifest), args.device)
    except ManifestError as e:
        _report_problems(args, "Manifest Problem", e.problems)
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
            args, "Sync Failed", [_describe_failure(result) for result in failures]
        )
        return ExitCode.REPOS_NOT_OK

    logger.info("Finished Sync!")
    if not args.no_pause:
        time.sleep(config.FINISH_PAUSE_SECONDS)
    return ExitCode.OK


def _describe_failure(result: SyncResult) -> str:
    """Returns one line describing a failed sync for the summary popup"""
    return f"{result.repo.name} ({result.tag}): {short_error(result.error)}"


if __name__ == "__main__":
    sys.exit(main())
