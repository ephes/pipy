"""Synthetic comparison observer: fresh-process committed SQLite read only.

Not a product inspector or Storage implementation. Both reference and native
fixtures use this neutral observer while their real owner holds the commit line.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    entry_id, task_id = int(sys.argv[2]), int(sys.argv[3])
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        entry = db.execute(
            "SELECT record,commit_seq FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        task = db.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()
        if entry is None or task is None:
            raise RuntimeError("Required committed fixture facts are absent")
        value = {
            "entry": json.loads(entry[0]),
            "commit_seq": entry[1],
            "task": json.loads(task[0]),
        }
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "pgid": os.getpgid(0),
                "argv": sys.argv,
                "executable": sys.executable,
                "python_version": sys.version,
                "sqlite_version": sqlite3.sqlite_version,
                "cwd": os.getcwd(),
                "helper_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "stored": value,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
