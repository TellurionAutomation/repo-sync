"""Repo Class with common tools.

Essentially a wrapper for the git-python Repo object,
with important functions for this script isolated.
"""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

import git

from repo_sync.repo_status import RepositoryStatus

_PEELED_SUFFIX = "^{}"

# Tags offered when a tag isn't on the remote, and how alike a spelling must
# be to count as close (difflib's ratio, 0 to 1)
_SUGGESTED_TAGS = 3
_CLOSE_TAG_RATIO = 0.6


# Network URLs: "scheme://[user@]host[:port]/path"
_NETWORK_URL = re.compile(
    r"^(?P<scheme>ssh|git|https?)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d*)?/(?P<path>.*)$",
    re.IGNORECASE,
)
# scp-style SSH: "[user@]host:path". A one-letter host is a Windows drive ("C:/...").
_SCP_URL = re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:\\]{2,}):(?!//)(?P<path>.*)$")
# The "user[:password]@" part of any "scheme://" URL in a piece of text
_URL_USERINFO = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s'\"]+)@", re.IGNORECASE)


def redact_urls(text: str) -> str:
    """Hides credentials in any URLs within text, so they are safe to show or log.

    Passwords are always hidden. For HTTP(S) the user name is hidden too,
    since a token is often passed there ("https://<token>@github.com/...").
    SSH user names such as "git@" are not secret and are kept.
    """

    def replace(match: re.Match[str]) -> str:
        scheme, userinfo = match["scheme"], match["userinfo"]
        if scheme.lower().startswith("http"):
            return f"{scheme}***@"
        user, has_password, _ = userinfo.partition(":")
        return f"{scheme}{user}:***@" if has_password else match[0]

    return _URL_USERINFO.sub(replace, text)


def _parse_url(url: str) -> tuple[str | None, str]:
    """Splits url into its protocol and a location that is the same for every spelling of it.

    A trailing slash or ".git" is dropped. Network remotes reduce to
    "host/path" whatever the user or port, so "git@github.com:org/repo.git"
    and "ssh://git@github.com/org/repo" are the same SSH location, and
    "https://github.com/org/repo" is that location over HTTPS. Local paths
    are normalized (case-insensitively on Windows), so "C:/git/repo.git" and
    "c:\\git\\repo" match there; their protocol is None.

    Returns:
        The protocol ("SSH", "HTTPS", "HTTP", "git" or None) and the location.
    """
    url = url.strip().rstrip("/\\").removesuffix(".git")
    if match := _NETWORK_URL.match(url):
        scheme = match["scheme"].lower()
        protocol = "git" if scheme == "git" else scheme.upper()
    elif match := _SCP_URL.match(url):
        protocol = "SSH"
    else:
        if PurePosixPath(url).is_absolute() or PureWindowsPath(url).is_absolute():
            url = os.path.normcase(os.path.normpath(url))
        return None, url
    return protocol, f"{match['host'].lower()}/{match['path'].strip('/')}"


def _version_key(tag: str) -> tuple[tuple[int, int | str], ...]:
    """Sorts tags by version, so "v1.10" comes after "v1.9" rather than before it"""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", tag)
        if part
    )


def _common_prefix_length(a: str, b: str) -> int:
    return len(os.path.commonprefix([a, b]))


def suggest_tags(tag: str, tags: list[str]) -> str:
    """Describes the tags on a remote that tag was probably meant to be.

    Tried in this order, stopping at the first that finds something:

    1. The same name in different capitalization, e.g. "V1.0" for "v1.0".
    2. Up to three close spellings, e.g. "v1.0.0" or "1.0" for "v1.0". They
       are ranked by how alike they are (ignoring case), then by how much of
       the start they share with tag, then by version, so "v1.0.0" beats
       "v9.1.0" even though difflib alone rates them the same.
    3. The three newest tags by version number, since ls-remote gives no dates.

    Returns:
        A sentence to add to a "tag not found" message.
    """
    if not tags:
        return "The remote has no tags."

    wanted = tag.lower()
    same_name = sorted(t for t in tags if t.lower() == wanted)
    if same_name:
        listed = " or ".join(f'"{t}"' for t in same_name)
        return f"Did you mean {listed}? Tags are case-sensitive."

    import difflib  # Only needed on this failure path

    matcher = difflib.SequenceMatcher(b=wanted)
    scored: list[tuple[float, str]] = []
    for candidate in tags:
        matcher.set_seq1(candidate.lower())
        # The cheap upper bounds skip most tags without the full comparison
        if (
            matcher.real_quick_ratio() >= _CLOSE_TAG_RATIO
            and matcher.quick_ratio() >= _CLOSE_TAG_RATIO
            and (ratio := matcher.ratio()) >= _CLOSE_TAG_RATIO
        ):
            scored.append((ratio, candidate))
    if scored:
        scored.sort(
            key=lambda item: (
                -item[0],
                -_common_prefix_length(item[1].lower(), wanted),
                _version_key(item[1]),
            )
        )
        listed = ", ".join(f'"{t}"' for _, t in scored[:_SUGGESTED_TAGS])
        return f"Closest tags on the remote: {listed}."

    newest = sorted(tags, key=_version_key, reverse=True)[:_SUGGESTED_TAGS]
    listed = ", ".join(f'"{t}"' for t in newest)
    return f"Newest tags on the remote: {listed}."


class Repository:
    """
    Repository class with functions for checking and updating tracked repos from the manifest repo

    Attributes:
        name (str): Human-readable name of the repository.
        url (str): URL for the remote repo
        local_dir (Path): Path the local directory to checked against the remote.
        repo (git.Repo): GitPython repo object. Used to access the repo object directly if the wrapper functions are not cutting it.
    """

    def __init__(self, name: str, url: str, local_dir: str | Path):
        """Create a Repository object"""
        self.name: str = name
        self.url = url
        self.local_dir = Path(local_dir)
        self.repo: git.Repo | None = None
        self._remote_tags: dict[str, str] | None = None

    def initialize_repo_object(self) -> git.Repo:
        """Opens the local repo and stores it on self.repo.

        The repo is only opened once; later calls return the stored object.

        Returns:
            The GitPython repo object for local_dir.

        Raises:
            git.InvalidGitRepositoryError: If local_dir is not a git repo.
            git.NoSuchPathError: If local_dir does not exist.
        """
        if self.repo is None:
            self.repo = git.Repo(self.local_dir)
        return self.repo

    @property
    def is_git_repo(self) -> bool:
        """Returns True if local_dir is a git repo, False if not or if it does not exist"""
        try:
            self.initialize_repo_object()
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            return False
        return True

    @property
    def local_dir_exists(self) -> bool:
        """Returns True if the local directory exists, False if not"""
        return self.local_dir.is_dir()

    def check_origin(self) -> None:
        """Checks that the local repo is a clone of url.

        Stops a sync from checking out tags from the wrong remote when a
        manifest row's URL has changed since the folder was cloned.

        A clone of the same repository over a different protocol (e.g. HTTPS
        when url is SSH) is not accepted either, since fetching and listing
        tags would then use different credentials.

        Raises:
            ValueError: If the repo has no "origin" remote, it points
                somewhere other than url, or it reaches the same repository
                over a different protocol.
            git.InvalidGitRepositoryError: If local_dir is not a git repo.
            git.NoSuchPathError: If local_dir does not exist.
        """
        repo = self.initialize_repo_object()
        expected = redact_urls(self.url)
        if "origin" not in [remote.name for remote in repo.remotes]:
            raise ValueError(
                f'{self.local_dir} has no "origin" remote; expected a clone of "{expected}"'
            )
        origin = repo.remotes.origin.url
        origin_protocol, origin_location = _parse_url(origin)
        protocol, location = _parse_url(self.url)
        if origin_location != location:
            raise ValueError(
                f'{self.local_dir} is a clone of "{redact_urls(origin)}", not "{expected}"'
            )
        if origin_protocol != protocol:
            raise ValueError(
                f"{self.local_dir} is a clone of the right repository, but over "
                f'{origin_protocol} ("{redact_urls(origin)}") while the manifest uses '
                f'{protocol} ("{expected}"). Either delete the folder so it is cloned '
                "again, or change the manifest URL to match"
            )

    def get_remote_tags(self, refresh: bool = False) -> dict[str, str]:
        """Lists tags on the remote and the commits they point to.

        Queries the remote directly with ``git ls-remote``, so this works
        whether or not a local clone exists. Annotated tags are resolved to the
        commit they reference rather than the tag object itself.

        The result is cached on this object, so the remote is only queried
        once unless a refresh is requested.

        Args:
            refresh: If True, ignore the cache and query the remote again.

        Returns:
            A mapping of tag name to commit SHA.

        Raises:
            git.GitCommandError: If the remote cannot be reached.
        """
        if not refresh and self._remote_tags is not None:
            return dict(self._remote_tags)

        output = git.cmd.Git().ls_remote("--tags", self.url)
        tags: dict[str, str] = {}
        peeled: dict[str, str] = {}

        for line in output.splitlines():
            sha, ref = line.split("\t", maxsplit=1)
            name = ref.removeprefix("refs/tags/")
            if name.endswith(_PEELED_SUFFIX):
                peeled[name.removesuffix(_PEELED_SUFFIX)] = sha
            else:
                tags[name] = sha

        # Peeled entries give the commit for annotated tags; prefer them.
        tags.update(peeled)
        self._remote_tags = tags
        return dict(tags)

    def clone_repo(self) -> git.Repo:
        """Clones the remote into local_dir.

        Missing parent directories are created by git.

        Returns:
            The GitPython repo object for the new clone.

        Raises:
            git.GitCommandError: If the clone fails, e.g. the remote cannot be
                reached or local_dir already exists and is not empty.
        """
        self.repo = git.Repo.clone_from(url=self.url, to_path=self.local_dir)
        return self.repo

    def checkout_tagged_commit(self, tag: str, destructive: bool = False) -> None:
        """Checks out the commit in the repo with the passed tag.

        The remote is only contacted if the tag is not known locally, and then
        only that one tag is fetched. If the remote tags were already listed
        with get_remote_tags, a tag missing from them fails without contacting
        the remote. This leaves the repo in a detached HEAD state at the
        tagged commit.
        By default local changes are not discarded, so a dirty working tree
        that conflicts with the checkout will make it fail.

        Args:
            tag: Name of the tag to check out.
            destructive: If True, discard changes to tracked files and
                overwrite untracked files that are in the way. Untracked files
                that do not conflict with the checkout are left alone.

        Raises:
            ValueError: If the tag does not exist locally or on the remote. The
                message suggests tags it may have been meant to be.
            git.InvalidGitRepositoryError: If local_dir is not a git repo.
            git.NoSuchPathError: If local_dir does not exist.
            git.GitCommandError: If the fetch or checkout fails.
        """
        repo = self.initialize_repo_object()

        if tag not in repo.tags:
            if self._remote_tags is not None and tag not in self._remote_tags:
                raise self._tag_not_found(tag, self._remote_tags)
            try:
                repo.remotes.origin.fetch(f"refs/tags/{tag}:refs/tags/{tag}")
            except git.GitCommandError:
                # Only spend a second remote call working out why the fetch failed.
                remote_tags = self.get_remote_tags()
                if tag not in remote_tags:
                    raise self._tag_not_found(tag, remote_tags) from None
                raise

        repo.git.checkout(tag, force=destructive)

    def _tag_not_found(self, tag: str, remote_tags: dict[str, str]) -> ValueError:
        """The error for a tag missing from the remote, suggesting what was meant"""
        return ValueError(
            f'Tag "{tag}" not found for repository "{self.name}". '
            f"{suggest_tags(tag, list(remote_tags))}"
        )

    def get_status(self, tag: str) -> RepositoryStatus:
        """Compares the local repo to tag without contacting the remote.

        Only tracked files count towards the repo being dirty; untracked files
        are ignored.

        Args:
            tag: The tag the repo is expected to be at.

        Returns:
            The status of the local repo.
        """
        if not self.is_git_repo:
            return RepositoryStatus.NOT_A_REPO
        repo = self.initialize_repo_object()
        if tag not in repo.tags or not repo.head.is_valid():
            return RepositoryStatus.OUT_OF_DATE
        try:
            tag_commit = repo.tags[tag].commit
        except (
            ValueError
        ):  # The tag points at a tree or blob, which HEAD can never be at
            return RepositoryStatus.OUT_OF_DATE
        if repo.head.commit != tag_commit:
            return RepositoryStatus.OUT_OF_DATE
        if repo.is_dirty(untracked_files=False):
            return RepositoryStatus.DIRTY
        return RepositoryStatus.CLEAN_MATCH

    def describe_head(self) -> str:
        """Returns ``git describe`` for HEAD, e.g. "v1.2", "v1.2-dirty" or "v1.2-3-gabc1234".

        Returns "NONE" if local_dir is not a git repo or HEAD has no tag behind it.
        """
        if not self.is_git_repo:
            return "NONE"
        try:
            return self.initialize_repo_object().git.describe("--tags", "--dirty")
        except git.GitCommandError:
            return "NONE"
