# repo-sync

repo-sync keeps a set of git repos on one or more machines pinned to known tagged versions, from a single manifest. Built for Hamilton workstations, but it works with any git repos. (Not affiliated with Hamilton Company; see [Trademarks](#trademarks).)

- **`sync`** clones any missing repos and checks out every repo at its tagged commit, discarding local changes.
- **`check`** compares each local repo to its tag without changing anything, and can write the results to a CSV for use by another application.

Maintained by [Tellurion](https://tellurionautomation.com) · Apache 2.0 · [Getting help](#getting-help)

## Installation

Requires Python 3.10 or later and [git](https://git-scm.com/). Create the virtual environment and install requirements:

**Windows**

```bat
setup_venv.bat
```

This creates `.venv` in the project root and installs the requirements. Pass a folder to put the venv somewhere else, for example `setup_venv.bat C:\repo-sync-venv`. It also sets the `REPO_SYNC_HOME` environment variable to the project folder, for batch files that need to find repo-sync. It's safe to run again: it does nothing unless the venv is missing or `requirements.txt` has changed.

To run the tool from VENUS or anywhere else, call `repo-sync.bat` with the usual arguments. It sets up the venv if needed, so you can skip the step above. See [Calling from VENUS](docs/reference.md#calling-from-venus).

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS and Linux, run the tool with `python sync.py …` rather than `py sync.py …`. Popups use tkinter, which some Linux distributions package separately (for example `python3-tk`). Without it, problems are still logged; only the popup is skipped.

## Getting started

1. Create a manifest: a CSV listing each repo, its URL, the folder it goes in, its tag and the devices it belongs to. See [The manifest](docs/manifest.md) for the format and an example.
2. Commit the manifest to its own git repo and clone that repo into `manifest/` in the project root, which is where the tool looks by default:

   ```bash
   git clone <manifest-repo-url> manifest
   ```

3. Make sure git credentials (SSH keys or stored HTTPS credentials) are set up for every repo in the manifest. The sync never prompts for a login.
4. Preview the sync, then run it:

   ```bat
   py sync.py sync main DEVICE_B --dry-run
   py sync.py sync main DEVICE_B
   ```

## Usage

```bat
py sync.py COMMAND BRANCH DEVICE [options]
```

or for a local untracked manifest:

```bat
py sync.py COMMAND DEVICE --local-manifest C:/path/to/manifest.csv [options]
```

`python -m repo_sync` also works.

- `BRANCH` is the [git branch](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell) of the manifest's repo that lists the tags to use.
- `DEVICE` is the device ID to sync, matched against the device columns in the manifest. `All` selects every repo, so it stops with an error if two rows share a directory for different devices. This allows you to have a single branch that controls multiple devices.

### Examples

```bat
:: Sync this computer's repos to the tags on the main branch
py sync.py sync main DEVICE_B

:: Show what would be synced, without changing anything
py sync.py sync main DEVICE_B --dry-run

:: Check one repo plus the critical repos, and write the results to a CSV
py sync.py check main DEVICE_B --repo Method-A --output C:/path/to/results.csv

:: Use a manifest that isn't in a repo (no BRANCH, no checkout)
py sync.py sync DEVICE_B --local-manifest C:/path/to/test-manifest.csv
```

## Documentation

- [The manifest](docs/manifest.md): columns, an example, where the manifest lives, different tags per device, and local manifests.
- [Reference](docs/reference.md): every option, what `sync` and `check` do step by step, exit codes, running on a schedule, logs, Git behavior, and configuration.
- [Contributing](CONTRIBUTING.md): running the tests.

## License

Copyright 2026 Tellurion LLC

Apache License 2.0. See [LICENSE.txt](LICENSE.txt).

### Trademarks

Hamilton, Hamilton Run Control and Hamilton Method Editor are trademarks of Hamilton Company. Excel is a trademark of Microsoft Corporation. They're named only to describe what repo-sync works with. repo-sync is an independent project by Tellurion LLC and is not affiliated with, endorsed by or sponsored by Hamilton Company or Microsoft.

## Getting help

Found a bug or have a feature request? Open an issue on GitHub.

Need help getting repo-sync set up, or want it adapted to your lab's workflow? Tellurion built and maintains this tool and can help. Email contact@tellurionautomation.com or use the form at https://tellurionautomation.com/contact.

Tellurion also takes on broader lab automation work, from method development and instrument integration to scheduling and fleet monitoring. If there's something your lab wants to automate and you're not sure where to start, get in touch.

## Acknowledgements

This work is inspired by an open release of a script written by the incredibly talented Kalpesh Gupta. repo-sync expands on the idea, but would not have been possible without the ideas he shared. Thank you Kalpesh!
