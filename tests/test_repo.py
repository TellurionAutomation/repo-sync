"""Tests for models.repo.Repository."""

from pathlib import Path
import subprocess

import git
import pytest

from git_helpers import head_commit, make_remote, run_git, tag_commit
from repo_sync.repo import Repository
from repo_sync.repo_status import RepositoryStatus


@pytest.fixture
def clone(tmp_path: Path, remote: Path) -> Repository:
    """A Repository that has already been cloned from remote"""
    repo = Repository("test", str(remote), tmp_path / "clone")
    repo.clone_repo()
    return repo


# --- __init__ -----------------------------------------------------------------


@pytest.mark.parametrize("local_dir", ["some/dir", Path("some/dir")])
def test_local_dir_is_always_a_path(local_dir: str | Path) -> None:
    repo = Repository("test", "url", local_dir)
    assert repo.local_dir == Path("some/dir")


def test_repo_object_starts_unset() -> None:
    assert Repository("test", "url", "dir").repo is None


# --- local_dir_exists / is_git_repo -------------------------------------------


def test_local_dir_exists(tmp_path: Path) -> None:
    assert Repository("test", "url", tmp_path).local_dir_exists
    assert not Repository("test", "url", tmp_path / "missing").local_dir_exists


def test_is_git_repo_false_for_missing_dir(tmp_path: Path) -> None:
    assert not Repository("test", "url", tmp_path / "missing").is_git_repo


def test_is_git_repo_false_for_plain_dir(tmp_path: Path) -> None:
    assert not Repository("test", "url", tmp_path).is_git_repo


def test_is_git_repo_false_for_dir_containing_something_named_git(
    tmp_path: Path,
) -> None:
    (tmp_path / "project.git").mkdir()
    assert not Repository("test", "url", tmp_path).is_git_repo


def test_is_git_repo_false_for_subdir_of_a_repo(clone: Repository) -> None:
    subdir = clone.local_dir / "sub"
    subdir.mkdir()
    assert not Repository("test", "url", subdir).is_git_repo


def test_is_git_repo_true_for_clone(clone: Repository) -> None:
    assert Repository("test", "url", clone.local_dir).is_git_repo


# --- initialize_repo_object ---------------------------------------------------


def test_initialize_repo_object_opens_once(clone: Repository) -> None:
    repo = Repository("test", "url", clone.local_dir)
    first = repo.initialize_repo_object()
    assert repo.repo is first
    assert repo.initialize_repo_object() is first


def test_initialize_repo_object_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(git.NoSuchPathError):
        Repository("test", "url", tmp_path / "missing").initialize_repo_object()


def test_initialize_repo_object_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(git.InvalidGitRepositoryError):
        Repository("test", "url", tmp_path).initialize_repo_object()


# --- clone_repo ---------------------------------------------------------------


def test_clone_creates_missing_parents(tmp_path: Path, remote: Path) -> None:
    repo = Repository("test", str(remote), tmp_path / "a" / "b" / "clone")
    cloned = repo.clone_repo()
    assert repo.repo is cloned
    assert (repo.local_dir / "f").read_text() == "two\n"


def test_clone_into_non_empty_dir_fails(tmp_path: Path, remote: Path) -> None:
    (tmp_path / "clone").mkdir()
    (tmp_path / "clone" / "junk.txt").write_text("x")
    with pytest.raises(git.GitCommandError):
        Repository("test", str(remote), tmp_path / "clone").clone_repo()


def test_clone_unreachable_remote_fails(tmp_path: Path) -> None:
    with pytest.raises(git.GitCommandError):
        Repository("test", str(tmp_path / "nope"), tmp_path / "clone").clone_repo()


# --- check_origin -------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "/", ".git", ".git/"])
def test_check_origin_accepts_same_remote(clone: Repository, suffix: str) -> None:
    Repository("test", clone.url + suffix, clone.local_dir).check_origin()


def test_check_origin_rejects_other_remote(clone: Repository, tmp_path: Path) -> None:
    other = make_remote(tmp_path / "other")
    with pytest.raises(ValueError, match="is a clone of"):
        Repository("test", str(other), clone.local_dir).check_origin()


def test_check_origin_rejects_missing_origin(clone: Repository) -> None:
    run_git(clone.local_dir, "remote", "remove", "origin")
    with pytest.raises(ValueError, match='no "origin" remote'):
        clone.check_origin()


# --- get_remote_tags ----------------------------------------------------------


def test_remote_tags_resolve_to_commits(tmp_path: Path, remote: Path) -> None:
    tags = Repository("test", str(remote), tmp_path / "unused").get_remote_tags()
    # v2 is annotated; it must map to the commit, not the tag object
    assert tags == {"v1": tag_commit(remote, "v1"), "v2": tag_commit(remote, "v2")}


def test_remote_tags_works_without_local_clone(tmp_path: Path, remote: Path) -> None:
    repo = Repository("test", str(remote), tmp_path / "missing")
    assert set(repo.get_remote_tags()) == {"v1", "v2"}
    assert not repo.local_dir_exists


def test_remote_tags_empty_for_untagged_remote(tmp_path: Path) -> None:
    untagged = tmp_path / "untagged"
    untagged.mkdir()
    run_git(untagged, "init", "-q")
    run_git(untagged, "commit", "-q", "--allow-empty", "-m", "one")
    assert (
        Repository("test", str(untagged), tmp_path / "unused").get_remote_tags() == {}
    )


def test_remote_tags_unreachable_remote(tmp_path: Path) -> None:
    with pytest.raises(git.GitCommandError):
        Repository(
            "test", str(tmp_path / "nope"), tmp_path / "unused"
        ).get_remote_tags()


def test_remote_tags_cached(
    tmp_path: Path, remote: Path, remote_calls: list[str]
) -> None:
    repo = Repository("test", str(remote), tmp_path / "unused")
    first = repo.get_remote_tags()
    assert repo.get_remote_tags() == first
    assert remote_calls == ["ls-remote"]


def test_remote_tags_refresh(
    tmp_path: Path, remote: Path, remote_calls: list[str]
) -> None:
    repo = Repository("test", str(remote), tmp_path / "unused")
    repo.get_remote_tags()
    run_git(remote, "tag", "v3")
    assert "v3" not in repo.get_remote_tags()
    assert "v3" in repo.get_remote_tags(refresh=True)
    assert remote_calls == ["ls-remote", "ls-remote"]


def test_remote_tags_cache_is_per_object(
    tmp_path: Path, remote: Path, remote_calls: list[str]
) -> None:
    Repository("a", str(remote), tmp_path / "a").get_remote_tags()
    Repository("b", str(remote), tmp_path / "b").get_remote_tags()
    assert remote_calls == ["ls-remote", "ls-remote"]


def test_remote_tags_returns_copy(tmp_path: Path, remote: Path) -> None:
    repo = Repository("test", str(remote), tmp_path / "unused")
    repo.get_remote_tags()["v1"] = "changed"
    assert repo.get_remote_tags()["v1"] == tag_commit(remote, "v1")


# --- checkout_tagged_commit ---------------------------------------------------


@pytest.mark.parametrize("tag", ["v1", "v2"])
def test_checkout_local_tag(
    clone: Repository, remote: Path, remote_calls: list[str], tag: str
) -> None:
    clone.checkout_tagged_commit(tag)
    assert head_commit(clone.local_dir) == tag_commit(remote, tag)
    assert clone.repo is not None and clone.repo.head.is_detached
    assert remote_calls == []


def test_checkout_opens_existing_clone(clone: Repository, remote: Path) -> None:
    fresh = Repository("test", str(remote), clone.local_dir)
    fresh.checkout_tagged_commit("v1")
    assert head_commit(clone.local_dir) == tag_commit(remote, "v1")


def test_checkout_without_clone(tmp_path: Path, remote: Path) -> None:
    with pytest.raises(git.NoSuchPathError):
        Repository("test", str(remote), tmp_path / "missing").checkout_tagged_commit(
            "v1"
        )


def test_checkout_fetches_only_the_new_tag(
    clone: Repository, remote: Path, remote_calls: list[str]
) -> None:
    run_git(remote, "tag", "v3", "v1")
    # v4 is on a new commit, on a branch the clone has never seen
    run_git(remote, "checkout", "-qb", "other")
    (remote / "f").write_text("four\n")
    run_git(remote, "commit", "-qam", "four")
    run_git(remote, "tag", "v4")

    clone.checkout_tagged_commit("v3")

    assert head_commit(clone.local_dir) == tag_commit(remote, "v3")
    assert remote_calls == ["fetch"]
    # Neither the other branch's commit nor its tag was downloaded
    assert "v4" not in run_git(clone.local_dir, "tag").split()
    with pytest.raises(subprocess.CalledProcessError):
        run_git(clone.local_dir, "cat-file", "-e", tag_commit(remote, "v4"))


def test_checkout_missing_tag_cold_cache(
    clone: Repository, remote_calls: list[str]
) -> None:
    with pytest.raises(ValueError, match='"nope"'):
        clone.checkout_tagged_commit("nope")
    assert remote_calls == ["fetch", "ls-remote"]


def test_checkout_missing_tag_warm_cache(
    clone: Repository, remote_calls: list[str]
) -> None:
    clone.get_remote_tags()
    remote_calls.clear()
    with pytest.raises(ValueError):
        clone.checkout_tagged_commit("nope")
    assert remote_calls == []


def test_checkout_stale_cache_needs_refresh(clone: Repository, remote: Path) -> None:
    clone.get_remote_tags()
    run_git(remote, "tag", "v3", "v1")
    with pytest.raises(ValueError):
        clone.checkout_tagged_commit("v3")
    clone.get_remote_tags(refresh=True)
    clone.checkout_tagged_commit("v3")
    assert head_commit(clone.local_dir) == tag_commit(remote, "v3")


def test_checkout_keeps_conflicting_changes_by_default(clone: Repository) -> None:
    (clone.local_dir / "f").write_text("local edit\n")
    with pytest.raises(git.GitCommandError):
        clone.checkout_tagged_commit("v1")
    assert (clone.local_dir / "f").read_text() == "local edit\n"


def test_checkout_destructive_discards_changes(clone: Repository, remote: Path) -> None:
    (clone.local_dir / "f").write_text("local edit\n")
    (clone.local_dir / "untracked.txt").write_text("keep me")
    clone.checkout_tagged_commit("v1", destructive=True)
    assert head_commit(clone.local_dir) == tag_commit(remote, "v1")
    assert (clone.local_dir / "f").read_text() == "one\n"
    assert (clone.local_dir / "untracked.txt").read_text() == "keep me"


def test_checkout_keeps_non_conflicting_changes(tmp_path: Path) -> None:
    remote = make_remote(tmp_path / "remote2")
    # "other" is identical at v3 and v4; only f differs between them
    (remote / "other").write_text("same\n")
    run_git(remote, "add", "other")
    run_git(remote, "commit", "-qm", "add other")
    run_git(remote, "tag", "v3")
    (remote / "f").write_text("four\n")
    run_git(remote, "commit", "-qam", "four")
    run_git(remote, "tag", "v4")

    repo = Repository("test", str(remote), tmp_path / "clone")
    repo.clone_repo()
    repo.checkout_tagged_commit("v3")
    (repo.local_dir / "other").write_text("local edit\n")
    repo.checkout_tagged_commit("v4")

    assert head_commit(repo.local_dir) == tag_commit(remote, "v4")
    assert (repo.local_dir / "other").read_text() == "local edit\n"


# --- get_status / describe_head -----------------------------------------------


def test_status_missing_dir(tmp_path: Path) -> None:
    repo = Repository("test", "url", tmp_path / "missing")
    assert repo.get_status("v1") == RepositoryStatus.NOT_A_REPO
    assert repo.describe_head() == "NONE"


def test_status_plain_dir(tmp_path: Path) -> None:
    repo = Repository("test", "url", tmp_path)
    assert repo.get_status("v1") == RepositoryStatus.NOT_A_REPO
    assert repo.describe_head() == "NONE"


@pytest.mark.parametrize("tag", ["v1", "v2"])
def test_status_clean_match(clone: Repository, tag: str) -> None:
    clone.checkout_tagged_commit(tag)
    assert clone.get_status(tag) == RepositoryStatus.CLEAN_MATCH
    assert clone.describe_head() == tag


def test_status_dirty(clone: Repository) -> None:
    clone.checkout_tagged_commit("v1")
    (clone.local_dir / "f").write_text("local edit\n")
    assert clone.get_status("v1") == RepositoryStatus.DIRTY
    assert clone.describe_head() == "v1-dirty"


def test_status_untracked_files_ignored(clone: Repository) -> None:
    clone.checkout_tagged_commit("v1")
    (clone.local_dir / "untracked.txt").write_text("x")
    assert clone.get_status("v1") == RepositoryStatus.CLEAN_MATCH
    assert clone.describe_head() == "v1"


def test_status_out_of_date(clone: Repository) -> None:
    clone.checkout_tagged_commit("v1")
    assert clone.get_status("v2") == RepositoryStatus.OUT_OF_DATE


def test_status_out_of_date_even_if_dirty(clone: Repository) -> None:
    clone.checkout_tagged_commit("v1")
    (clone.local_dir / "f").write_text("local edit\n")
    assert clone.get_status("v2") == RepositoryStatus.OUT_OF_DATE


def test_status_tag_not_fetched(
    clone: Repository, remote: Path, remote_calls: list[str]
) -> None:
    run_git(remote, "tag", "v3", "v1")
    assert clone.get_status("v3") == RepositoryStatus.OUT_OF_DATE
    assert remote_calls == []


def test_status_matches_when_two_tags_share_a_commit(
    clone: Repository, remote: Path
) -> None:
    run_git(remote, "tag", "v1-alias", "v1")
    clone.checkout_tagged_commit("v1-alias")
    assert clone.get_status("v1") == RepositoryStatus.CLEAN_MATCH
    assert clone.get_status("v1-alias") == RepositoryStatus.CLEAN_MATCH


@pytest.mark.parametrize("target", ["HEAD^{tree}", "HEAD:f"])
def test_tag_on_tree_or_blob(clone: Repository, remote: Path, target: str) -> None:
    run_git(remote, "tag", "not-a-commit", target)
    with pytest.raises(git.GitCommandError):
        clone.checkout_tagged_commit("not-a-commit")
    assert clone.get_status("not-a-commit") == RepositoryStatus.OUT_OF_DATE


def test_status_empty_repo(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    run_git(tmp_path / "empty", "init", "-q")
    repo = Repository("test", "url", tmp_path / "empty")
    assert repo.get_status("v1") == RepositoryStatus.OUT_OF_DATE
    assert repo.describe_head() == "NONE"


def test_describe_between_tags(clone: Repository) -> None:
    clone.checkout_tagged_commit("v2")
    run_git(clone.local_dir, "commit", "-q", "--allow-empty", "-m", "after v2")
    assert clone.describe_head().startswith("v2-1-g")
