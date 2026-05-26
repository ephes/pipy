"""Metadata-only session branching helper for the native pipy runtime.

Pi exposes session branching through `agent-session.ts` so a user can
fork from a prior conversation tree. Pipy's archive is metadata-first
and immutable, so we slopfork the useful subset — a referenceable
parent pointer — through pipy-owned Python boundaries, not as a
literal port:

- `SessionBranchReference` is a frozen value object carrying the
  parent session's stable identifiers (id, slug, agent) plus a
  freshly minted child id and an optional `branch_label`. No
  conversation body, no provider text, no archive payload is copied.
- `branch_from(parent, *, child_id=None, branch_label=None)` is the
  factory used by callers; it validates parent metadata, derives a
  deterministic child id when one is not supplied, and returns the
  value object.
- `fork_from(...)` is an alias kept for parity with Pi terminology
  (`fork_from` and `branch_from` are interchangeable in pipy).

The session recorder may, in a later slice, write a
`session.branched_from` lifecycle event containing only
`SessionBranchReference.archive_metadata()`. Until that wiring lands,
this module is consumable from tests and CLI helpers, while keeping
the archive strictly metadata-only.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable


@runtime_checkable
class _BranchableSessionMetadata(Protocol):
    """Structural interface for finalized-record metadata used as parent."""

    @property
    def session_id(self) -> str: ...
    @property
    def slug(self) -> str: ...
    @property
    def agent(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SessionBranchReference:
    """Metadata-only pointer from a child session to its parent.

    The reference deliberately does NOT carry any of the parent's
    conversation body, provider text, tool payloads, or file
    contents; the child session record will only embed the
    archive-safe metadata returned by `archive_metadata()`.
    """

    parent_session_id: str
    parent_slug: str
    parent_agent: str
    child_session_id: str
    branch_label: str | None = None

    SESSION_ID_MAX_LENGTH: ClassVar[int] = 128
    BRANCH_LABEL_MAX_LENGTH: ClassVar[int] = 64
    AGENT_MAX_LENGTH: ClassVar[int] = 64
    SLUG_MAX_LENGTH: ClassVar[int] = 128

    def __post_init__(self) -> None:
        if not isinstance(self.parent_session_id, str) or not self.parent_session_id:
            raise ValueError("parent_session_id must be a non-empty string")
        if len(self.parent_session_id) > self.SESSION_ID_MAX_LENGTH:
            raise ValueError(
                f"parent_session_id exceeds {self.SESSION_ID_MAX_LENGTH} characters"
            )
        if not isinstance(self.parent_slug, str) or not self.parent_slug:
            raise ValueError("parent_slug must be a non-empty string")
        if len(self.parent_slug) > self.SLUG_MAX_LENGTH:
            raise ValueError(f"parent_slug exceeds {self.SLUG_MAX_LENGTH} characters")
        if not isinstance(self.parent_agent, str) or not self.parent_agent:
            raise ValueError("parent_agent must be a non-empty string")
        if len(self.parent_agent) > self.AGENT_MAX_LENGTH:
            raise ValueError(f"parent_agent exceeds {self.AGENT_MAX_LENGTH} characters")
        if not isinstance(self.child_session_id, str) or not self.child_session_id:
            raise ValueError("child_session_id must be a non-empty string")
        if self.child_session_id == self.parent_session_id:
            raise ValueError("child_session_id must differ from parent_session_id")
        if len(self.child_session_id) > self.SESSION_ID_MAX_LENGTH:
            raise ValueError(
                f"child_session_id exceeds {self.SESSION_ID_MAX_LENGTH} characters"
            )
        if self.branch_label is not None:
            if not isinstance(self.branch_label, str) or not self.branch_label:
                raise ValueError(
                    "branch_label must be a non-empty string or None"
                )
            if len(self.branch_label) > self.BRANCH_LABEL_MAX_LENGTH:
                raise ValueError(
                    f"branch_label exceeds {self.BRANCH_LABEL_MAX_LENGTH} characters"
                )

    def archive_metadata(self) -> dict[str, str | None]:
        """Return the dict allowed to ride in safe session payloads."""

        return {
            "parent_session_id": self.parent_session_id,
            "parent_slug": self.parent_slug,
            "parent_agent": self.parent_agent,
            "child_session_id": self.child_session_id,
            "branch_label": self.branch_label,
        }


def branch_from(
    parent: _BranchableSessionMetadata,
    *,
    child_session_id: str | None = None,
    branch_label: str | None = None,
) -> SessionBranchReference:
    """Construct a metadata-only branch reference rooted at `parent`.

    When `child_session_id` is omitted, a 32-hex-character id is
    minted via `secrets.token_hex(16)`. The function does not touch
    the filesystem, the session recorder, or any provider; it returns
    a pure value object.
    """

    if not isinstance(parent.session_id, str) or not parent.session_id:
        raise ValueError("parent.session_id must be a non-empty string")
    if not isinstance(parent.slug, str) or not parent.slug:
        raise ValueError("parent.slug must be a non-empty string")
    if not isinstance(parent.agent, str) or not parent.agent:
        raise ValueError("parent.agent must be a non-empty string")

    if child_session_id is None:
        child_session_id = secrets.token_hex(16)

    return SessionBranchReference(
        parent_session_id=parent.session_id,
        parent_slug=parent.slug,
        parent_agent=parent.agent,
        child_session_id=child_session_id,
        branch_label=branch_label,
    )


def fork_from(
    parent: _BranchableSessionMetadata,
    *,
    child_session_id: str | None = None,
    branch_label: str | None = None,
) -> SessionBranchReference:
    """Alias for `branch_from` matching Pi's `fork` terminology."""

    return branch_from(
        parent,
        child_session_id=child_session_id,
        branch_label=branch_label,
    )
