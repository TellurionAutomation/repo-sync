# The manifest

The manifest is a CSV file that dictates what repos should be synced/checked, and which commit should be used for comparison.

## Columns

The manifest needs the columns below. They're found by header name (ignoring case), so their order doesn't matter, and other columns are ignored.

| Column | Description |
| --- | --- |
| `Repo` | Name of the repo, used in messages. |
| `URL` | Remote URL: It's recommended to use the scp-like `git@host:path` form, but this field will also accept `https://`, `ssh://`, `git://`, `file://`, or an absolute local path. |
| `Directory` | Where the repo is checked out locally. |
| `Tag` | The [git tag](https://git-scm.com/book/en/v2/Git-Basics-Tagging) the repo is synced to. A selected repo with a blank tag is an error. |
| `Critical` | `TRUE` for repos that `check` always checks, even with the `--repo` flag. Combined with `--repo`, this lets `check` skip unrelated repos on long manifests. |
| `Device1`, `Device2`, … | Device IDs this repo belongs to. A device ID can be in any of the device columns, and `All` matches every device. Useful if your manifest is managing multiple machines, as it allows for per-machine control from a single manifest commit. A device column is named `Device` plus an optional number, so add as many as you need; by convention they go last. Other headers starting with "Device", such as "Device notes", are ignored. |

Every problem in the manifest is reported at once, so they can all be fixed together.

## Example

| Repo | URL | Directory | Tag | Critical | Device1 | Device2 |
| --- | --- | --- | --- | --- | --- | --- |
| Repo A | git@github.com:team/repo-a.git | C:\Repos\Repo-A | v1.2 | TRUE | All |  |
| Repo B | git@github.com:team/repo-b.git | C:\Repos\Repo-B | v3.1 |  | DEVICE_B | DEVICE_C |

## Where the manifest lives

Keep the manifest in its own git repo. Each branch of that repo can list different tags, and the branch to use is passed on the command line as `BRANCH`.

Clone the repo that holds your manifest into `manifest/` in the project root, which is where the tool looks by default. That folder is ignored by git. To keep it somewhere else, set `MANIFEST_PATH` in your [local settings](reference.md#configuration) or pass `--manifest`.

```bash
git clone <manifest-repo-url> manifest
```

Credentials (SSH keys or stored HTTPS credentials) must already be set up for every repo in the manifest. The sync never prompts for a login. See [Git behavior](reference.md#git-behavior).

## Different tags on different devices

To sync one folder to a different tag per device, give the repo one row per tag. Each row uses the same `Directory`, and they cover different devices:

| Repo | URL | Directory | Tag | Critical | Device1 | Device2 |
| --- | --- | --- | --- | --- | --- | --- |
| Method-A | git@github.com:team/method-a.git | C:\Methods\Method-A | v1.2 |  | DEVICE_A | DEVICE_B |
| Method-A | git@github.com:team/method-a.git | C:\Methods\Method-A | v1.3 |  | DEVICE_C |  |

Here `DEVICE_A` and `DEVICE_B` get `v1.2`, and `DEVICE_C` gets `v1.3`. The rows must not overlap: if a device matches both rows (because it's listed twice, or one row uses `All`), that device's run stops before changing anything and names the shared folder. For the same reason, running with `ALL` as the device doesn't work with this layout.

If whole groups of devices differ on many repos, a separate manifest branch for each group is usually easier to maintain.

## Local manifest

If you'd rather just have a single local manifest (for example, for testing), you can run the script with a `--local-manifest` flag to indicate that you'd like to use a manifest file on the machine that is not currently in a repository. `BRANCH` is left out, and no checkout is done:

```bat
py sync.py sync DEVICE_B --local-manifest C:/path/to/test-manifest.csv
```
