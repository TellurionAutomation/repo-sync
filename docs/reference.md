# Reference

Command options, what each command does, and how to run and configure repo-sync on lab machines. For the manifest format, see [The manifest](manifest.md).

- [Options](#options)
- [How it works](#how-it-works)
- [Exit codes](#exit-codes)
- [Running on a schedule](#running-on-a-schedule)
- [One run at a time](#one-run-at-a-time)
- [Logs](#logs)
- [Git behavior](#git-behavior)
- [Configuration](#configuration)

## Options

These options work with both commands:

| Option | Description |
| --- | --- |
| `--no-pull` | Use the local copy of `BRANCH` without fetching it from the remote first. |
| `--manifest PATH` | Path to the manifest. Default: `manifest/manifest.csv` in the project root, or `MANIFEST_PATH` from your [local settings](#configuration). |
| `--local-manifest PATH` | Read this manifest as it is, without checking out a branch. Leave out `BRANCH`. Can't be combined with `--manifest` or `--no-pull`. |
| `--log-dir PATH` | Folder for the log file. Default: `logs/` in the project root. |
| `--no-popup` | Only log problems, without also showing a popup. Use this for unattended runs. |

These options only work with `sync`:

| Option | Description |
| --- | --- |
| `--dry-run` | List what would be synced without changing any repos. |
| `--workers N` | Number of repos synced at the same time. Default: 4. |
| `--no-pause` | Exit straight away instead of pausing for 5 seconds at the end. |

These options only work with `check`:

| Option | Description |
| --- | --- |
| `--output PATH` | CSV file to write the results to. No file is written if this is left out. |
| `--repo NAME` | Check only this repo plus the critical repos. If the repo isn't found, every repo is checked. |

## How it works

The summaries below provide an overview of how the scripts work generally.

### `sync`

1. Checks that nothing is open that would make the checkout fail. This tool was originally written for managing Hamilton computers, so by default it checks that Hamilton Run Control and Hamilton Method Editor are closed, and that the manifest isn't open in Excel. If something is open, the sync stops and asks the user to close it. To add or remove applications from this check, set `SYNC_INCOMPATIBLE_APPS` (applications that must be closed) or `MANIFEST_EDITORS` (applications that must not have the manifest open) in your [local settings](#configuration).
2. Checks out the newest manifest from `BRANCH`, unless `--no-pull` or `--local-manifest` is used.
3. Selects the repos for `DEVICE`.
4. Syncs those repos several at a time. Repos that don't exist yet are cloned. A folder that is already a clone of a different remote (for example, because the repo's URL changed in the manifest) is reported as a failure and left alone; delete or re-clone it to fix it. Each repo is then checked out at its tag, which leaves it on a detached HEAD. Changes to tracked files are discarded. Untracked files are left alone unless they're in the way of the checkout. The remote is only contacted if the tag isn't known locally.
5. Reports any failures in a popup and in the log.

### `check`

Like `sync`, `check` first makes sure the manifest isn't open in an editor, checks out the newest manifest from `BRANCH` (unless `--no-pull` or `--local-manifest` is used) and selects the repos for `DEVICE`. It doesn't require applications defined in `SYNC_INCOMPATIBLE_APPS` in the [configuration](#configuration) to be closed. Rather than updating the repos, `check` just compares the current state against what is expected from the manifest. It never contacts the repos' remotes, so a tag that hasn't been fetched yet counts as out of date. Repos will be assigned one of the following results depending on what is found:

- `OK`: the repo is at its tag, with no changes to tracked files.
- `DIRTY`: the repo is at its tag, but tracked files have been changed.
- `OUT OF DATE`: the repo is not at its tag.
- `NOT A REPO`: the directory is missing or isn't a git repo.

With `--output`, the results are written to a CSV, which can be used by another program to assess if the machine is synced to the expected environment. The CSV has one row per repo, with these columns: Repo Name, Directory, Manifest Tag, Local Commit Tag, Expected Tag, Working Tree Clean. The header row also holds, in this order, the manifest branch that was checked against (or the file's path for `--local-manifest`), the time of the check, the manifest's commit hash, and whether the manifest had uncommitted changes (`True`/`False`, or `NONE` for a manifest that isn't in a git repo).

## Exit codes

Both commands use the same exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Every repo synced, or every repo checked is clean and at its tag. |
| 1 | Some repos failed to sync, or aren't clean and at their tag. |
| 2 | Invalid command line arguments. |
| 3 | Not started: a problem with the manifest, the pull failed, a blocking application is open, or another run is already in progress. |
| 4 | Unexpected error in the script. |
| 130 | Stopped with Ctrl+C. |

## Running on a schedule

For unattended runs, pass `--no-popup` (problems go to the log instead of a window nobody will close) and `--no-pause`. Use the virtual environment's Python directly so nothing needs activating.

**Windows (Task Scheduler):**

```bat
schtasks /Create /TN "repo-sync" /SC DAILY /ST 06:00 /TR "\"C:\repo-sync\.venv\Scripts\python.exe\" \"C:\repo-sync\sync.py\" sync main DEVICE_B --no-popup --no-pause"
```

**macOS / Linux (cron):**

```
0 6 * * * /opt/repo-sync/.venv/bin/python /opt/repo-sync/sync.py sync main DEVICE_B --no-popup --no-pause
```

Things to know:

- **Run the task as the user whose git credentials are set up.** SSH keys and stored HTTPS credentials belong to a user account, and the sync never prompts for a login.
- **A blocked run doesn't retry by itself.** If Hamilton Run Control or Method Editor is open at the scheduled time, the run stops with exit code 3 and changes nothing. The next scheduled run tries again.
- **Overlapping runs are safe.** If a manual run is still going when the scheduled one starts, the second run exits with code 3 (see [One run at a time](#one-run-at-a-time)).
- **Check the result by exit code or log.** 0 means everything synced, 1 means some repos failed, and 3 means it didn't start. Each run writes its own file in `logs/`.

## One run at a time

Only one `sync` or `check` runs on a computer at a time. A second run, such as a manual run while a scheduled one is going, stops with exit code 3 and says another run is in progress. The lock is held on `repo-sync.lock` in the project root (`LOCK_PATH` in the [settings](#configuration)) and is released when the run ends, even if it crashes.

## Logs

Messages are shown on the console and also written to a timestamped log file in `logs/` (or `--log-dir`), for example `20261101_181008_sync.log` or `20261101_181008_check.log`.

Each run logs the manifest's commit hash, so results can be traced back to the exact manifest they used. If the manifest file has uncommitted changes, which can only happen with `--local-manifest`, that line is a warning.

## Git behavior

- Username/password prompts are turned off, including Git Credential Manager's login window. Credentials that are already stored still work.
- SSH runs without prompts and gives up connecting after 30 seconds. Hosts seen for the first time are trusted. A host whose key has changed is rejected.
- Connections that stall for about 60 seconds are dropped.

If `GIT_SSH_COMMAND`, `GIT_SSH` or `core.sshCommand` is already set, the SSH settings are left alone. Timeouts and other settings can be changed in your [local settings](#configuration).

## Configuration

Default settings are in [repo_sync/config.py](../repo_sync/config.py):

| Setting | Default | What it controls |
| --- | --- | --- |
| `MANIFEST_PATH` | `manifest/manifest.csv` | Where the manifest is read from |
| `MAX_WORKERS` | 4 | Repos synced at the same time |
| `FINISH_PAUSE_SECONDS` | 5 | Pause before the console closes after a sync |
| `GIT_CONNECT_TIMEOUT_SECONDS` | 30 | How long SSH waits to connect |
| `GIT_STALL_TIMEOUT_SECONDS`, `GIT_STALL_MIN_BYTES_PER_SECOND` | 60, 1000 | When a stalled transfer is dropped |
| `LOCK_PATH` | `repo-sync.lock` | The file that stops two runs overlapping |
| `LoggingConfig.LOG_DIR` | `logs/` | Where log files are written |
| `SYNC_INCOMPATIBLE_APPS` | Hamilton Run Control, Method Editor | Applications that must be closed before a sync |
| `MANIFEST_EDITORS` | Excel | Applications that must not have the manifest open |

Paths are relative to the project root.

Don't edit `config.py` to change them on one machine. Copy [repo_sync/config_local.example.py](../repo_sync/config_local.example.py) to `repo_sync/config_local.py` and set the values there. Anything in `config_local.py` replaces the default of the same name. The file isn't tracked by git, so pulling updates to the tool never conflicts with local settings.

For example, to use the manifest location on Hamilton computers and add an application to the blocking list:

```python
from pathlib import Path

from repo_sync.config import *  # noqa: F401,F403

MANIFEST_PATH = Path("C:/Program Files (x86)/HAMILTON/manifest/manifest.csv")
SYNC_INCOMPATIBLE_APPS["Foo.exe"] = "Foo"
```

The `import *` line makes the defaults available, so a setting can be extended rather than replaced, as `SYNC_INCOMPATIBLE_APPS` is here.
