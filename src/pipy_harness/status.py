"""Dependency-neutral harness status vocabulary."""

from enum import StrEnum


class HarnessStatus(StrEnum):
    """Small run status vocabulary shared by harness and native events."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
