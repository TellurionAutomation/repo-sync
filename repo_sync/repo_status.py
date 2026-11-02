"""Status of a local repository compared to its target tag."""

from enum import Enum


class RepositoryStatus(Enum):
    """Status of a local repository compared to its target tag."""

    CLEAN_MATCH = "clean match"  # At the target tag with no local changes
    DIRTY = "dirty"  # At the target tag, but tracked files have local changes
    OUT_OF_DATE = "out of date"  # Not at the target tag
    NOT_A_REPO = "not a repo"  # Directory is missing or is not a git repo
