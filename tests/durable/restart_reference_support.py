"""Shared native producer helpers: source attestation and a recording store.

The restart-parking producer that used to live here was withdrawn with its
phase (R15-1b); release 1.5 removed parking and ``retryTask()``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def origins(root):
    import hashlib

    result = {}
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename is not None:
            p = Path(filename).resolve()
            # Repository sources only: an interpreter environment inside the
            # checkout (an in-repo .venv) is not part of the producer closure.
            if (
                p.is_relative_to(root)
                and p.suffix == ".py"
                and p.relative_to(root).parts[0] in {"src", "tests", "scripts"}
            ):
                result[str(p.relative_to(root))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
    return result


class Recording:
    def __init__(self, inner):
        self.inner, self.id, self.last = inner, None, None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        if self.id is not None:
            row = self.inner.task(self.id)
            self.last = None if row is None else row.to_tree()
        return self.inner.close()
