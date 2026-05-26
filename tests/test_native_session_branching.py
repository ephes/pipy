"""Session branching helper contract tests (E3 parity)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pipy_harness.native.session_branching import (
    SessionBranchReference,
    branch_from,
    fork_from,
)


@dataclass(frozen=True)
class _ParentMetadata:
    session_id: str
    slug: str
    agent: str


def test_branch_from_returns_value_object_with_archive_safe_fields() -> None:
    parent = _ParentMetadata(
        session_id="parent-0123456789abcdef",
        slug="parent-slug",
        agent="pipy-native",
    )

    ref = branch_from(parent, branch_label="experiment-a")

    assert isinstance(ref, SessionBranchReference)
    assert ref.parent_session_id == parent.session_id
    assert ref.parent_slug == parent.slug
    assert ref.parent_agent == parent.agent
    assert ref.child_session_id != parent.session_id
    assert ref.branch_label == "experiment-a"


def test_branch_from_archive_metadata_keys_are_stable_and_safe() -> None:
    parent = _ParentMetadata(
        session_id="parent-0123",
        slug="parent-slug",
        agent="pipy-native",
    )

    ref = branch_from(parent)

    metadata = ref.archive_metadata()
    assert set(metadata.keys()) == {
        "parent_session_id",
        "parent_slug",
        "parent_agent",
        "child_session_id",
        "branch_label",
    }
    assert "secret" not in str(metadata).lower()


def test_branch_from_mints_unique_child_session_id_by_default() -> None:
    parent = _ParentMetadata(
        session_id="parent-0123",
        slug="parent-slug",
        agent="pipy-native",
    )

    first = branch_from(parent)
    second = branch_from(parent)

    assert first.child_session_id != second.child_session_id
    assert len(first.child_session_id) == 32


def test_branch_from_accepts_explicit_child_session_id() -> None:
    parent = _ParentMetadata(
        session_id="parent-0123",
        slug="parent-slug",
        agent="pipy-native",
    )

    ref = branch_from(parent, child_session_id="child-abc123")

    assert ref.child_session_id == "child-abc123"


def test_branch_from_rejects_matching_child_and_parent_ids() -> None:
    parent = _ParentMetadata(
        session_id="dupe-id",
        slug="parent-slug",
        agent="pipy-native",
    )

    with pytest.raises(ValueError):
        branch_from(parent, child_session_id="dupe-id")


def test_branch_from_rejects_blank_parent_fields() -> None:
    parent = _ParentMetadata(session_id="", slug="slug", agent="pipy-native")
    with pytest.raises(ValueError):
        branch_from(parent)


def test_branch_from_rejects_overlong_branch_label() -> None:
    parent = _ParentMetadata(
        session_id="parent-id",
        slug="parent-slug",
        agent="pipy-native",
    )
    too_long = "x" * (SessionBranchReference.BRANCH_LABEL_MAX_LENGTH + 1)

    with pytest.raises(ValueError):
        branch_from(parent, branch_label=too_long)


def test_branch_from_rejects_empty_branch_label() -> None:
    parent = _ParentMetadata(
        session_id="parent-id",
        slug="parent-slug",
        agent="pipy-native",
    )

    with pytest.raises(ValueError):
        branch_from(parent, branch_label="")


def test_fork_from_is_alias_for_branch_from() -> None:
    parent = _ParentMetadata(
        session_id="parent-id",
        slug="parent-slug",
        agent="pipy-native",
    )

    forked = fork_from(parent, child_session_id="c1", branch_label="lbl")
    branched = branch_from(parent, child_session_id="c1", branch_label="lbl")

    assert forked == branched
