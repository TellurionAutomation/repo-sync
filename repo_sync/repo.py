"""Repo Class with common tools.

Essentially a wrapper for the git-python Repo object,
with important functions for this script isolated.
"""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import git

from repo_sync.repo_status import RepositoryStatus

_PEELED_SUFFIX = "^{}"


def _normalize_url(url: str) -> str:
    """Reduces url to a form where two spellings of the same remote compare equal.

    A trailing slash or ".git" is dropped, and local paths are normalized
    (case-insensitively on Windows), so "C:/git/repo.git" and "c:\\git\\repo"
    match there.
    """
    url = url.strip().rstrip("/\\").removesuffix(".git")
    if PurePosixPath(url).is_absolute() or PureWindowsPath(url).is_absolute():
        return os.path.normcase(os.path.normpath(url))
    return url


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

        Raises:
            ValueError: If the repo has no "origin" remote or it points
                somewhere other than url.
            git.InvalidGitRepositoryError: If local_dir is not a git repo.
            git.NoSuchPathError: If local_dir does not exist.
        """
        repo = self.initialize_repo_object()
        if "origin" not in [remote.name for remote in repo.remotes]:
            raise ValueError(
                f'{self.local_dir} has no "origin" remote; expected a clone of "{self.url}"'
            )
        origin = repo.remotes.origin.url
        if _normalize_url(origin) != _normalize_url(self.url):
            raise ValueError(
                f'{self.local_dir} is a clone of "{origin}", not "{self.url}"'
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
            ValueError: If the tag does not exist locally or on the remote.
            git.InvalidGitRepositoryError: If local_dir is not a git repo.
            git.NoSuchPathError: If local_dir does not exist.
            git.GitCommandError: If the fetch or checkout fails.
        """
        repo = self.initialize_repo_object()

        if tag not in repo.tags:
            not_found = ValueError(
                f'Tag "{tag}" not found for repository "{self.name}"'
            )
            if self._remote_tags is not None and tag not in self._remote_tags:
                raise not_found
            try:
                repo.remotes.origin.fetch(f"refs/tags/{tag}:refs/tags/{tag}")
            except git.GitCommandError:
                # Only spend a second remote call working out why the fetch failed.
                if tag not in self.get_remote_tags():
                    raise not_found from None
                raise

        repo.git.checkout(tag, force=destructive)

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
