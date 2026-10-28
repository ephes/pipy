"""Real Session fixtures and bounded observation for cold task migrations."""

import asyncio
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery
from pipy_harness.native.durable.tasks import define_task

DEFAULT_INPUT = object()

LOGICAL_TABLES = (
    "conversations",
    "entries",
    "tasks",
    "submissions",
    "documents",
    "durable_metadata",
    "record_ids",
    "submission_requests",
    "document_revisions",
)


async def unexpected_abort(*_args):
    raise AssertionError("This fixture does not enable abort dispatch")


class ObservedStorage:
    """Forward actual storage, retaining admission and physical close witnesses."""

    def __init__(self, inner):
        self.inner = inner
        self.commits = []
        self.mints = []
        self.before_commit = None
        self.after_commit = None
        self.final_tasks = ()
        self.closed = threading.Event()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def mint_id(self):
        value = self.inner.mint_id()
        self.mints.append(value)
        return value

    def commit(self, writes):
        self.commits.append(tuple(writes))
        if self.before_commit is not None:
            self.before_commit(writes)
        result = self.inner.commit(writes)
        if self.after_commit is not None:
            self.after_commit(writes)
        return result

    def close(self):
        try:
            self.final_tasks = self.inner.scan_tasks(TaskQuery({}), 100).items
        finally:
            self.inner.close()
            self.closed.set()


def sqlite_rows(path):
    """Read every logical column, including revisions, in one SQL snapshot."""
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        rows = {
            table: tuple(db.execute("SELECT * FROM " + table + " ORDER BY rowid"))
            for table in LOGICAL_TABLES
        }
        rows["durable_schema"] = tuple(db.execute("SELECT * FROM durable_schema"))
        return rows


class Rig:
    def __init__(self, backend, root):
        self.backend = backend
        self.path = root / "migration.sqlite"
        inner = (
            MemoryStorage()
            if backend == "memory"
            else open_sqlite_storage(str(self.path), mode="create")
        )
        self.storage = ObservedStorage(inner)
        self.session = Session(self.storage)
        self.scheduler = None
        self.conversation = self.session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        ).id

    def create(self, definition, value=DEFAULT_INPUT):
        return self.session.commit(
            lambda tx: tx.create_task(
                definition,
                {"input": [1, None]} if value is DEFAULT_INPUT else value,
                ownership={"kind": "conversation"},
                conversation_id=self.conversation,
            )
        )

    def record(self, task_id):
        return self.session.read_on_line(lambda: self.storage.task(task_id).to_tree())

    def change(self, task_id, edit):
        def apply(tx):
            row = tx.task(task_id).to_tree()
            edit(row)
            tx.set_task(row)

        self.session.commit(apply)

    def snapshot(self):
        def capture():
            inner = self.storage.inner
            if self.backend == "sqlite":
                return sqlite_rows(self.path)
            with inner._lock:
                # Published states do not mutate. Holding the actual incarnation
                # also detects an otherwise identical Memory commit/mint.
                return inner._state

        return self.session.read_on_line(capture)

    def silence(self):
        return self.snapshot(), len(self.storage.commits), len(self.storage.mints)

    def assert_silent(self, before):
        state, commits, mints = before
        after = self.snapshot()
        if self.backend == "memory":
            assert after is state
        else:
            assert after == state
        assert len(self.storage.commits) == commits
        assert len(self.storage.mints) == mints

    def close(self):
        self.session.close(timeout=5)
        assert self.storage.closed.is_set()
        if self.scheduler is not None:
            assert not self.scheduler._thread.is_alive()


def definition(name="migration.work", version=1, *, phase=None, migrate=None):
    async def idle(_task, _runtime, context):
        await context.signal.wait()

    options = {} if migrate is None else {"migrate": migrate}
    return define_task(
        name,
        version,
        lambda _value: {"phase": "start", "saved": [1, None]},
        {"start": idle if phase is None else phase},
        unexpected_abort,
        **options,
    )


def pause(scheduler):
    asyncio.run(scheduler.apause())


def observe_reservation(scheduler):
    """Retain real pass/transaction visits; never replace candidate behavior."""
    finished = threading.Event()
    visits = []
    passes = []
    original_reserve = scheduler._reserve
    original_candidate = scheduler._candidate

    async def candidate(tx, row, *args):
        visits.append((len(passes), id(tx), row["id"]))
        return await original_candidate(tx, row, *args)

    async def reserve():
        passes.append(None)
        try:
            result = await original_reserve()
            passes[-1] = None if result is None else result.task_id
            return result
        finally:
            finished.set()

    scheduler._candidate = candidate
    scheduler._reserve = reserve
    return finished, visits, passes


OPENER = r"""
import hashlib
from pathlib import Path
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.ownership import StorageOwnershipError
try:
    storage = open_sqlite_storage(sys.argv[1], mode='open')
except StorageOwnershipError:
    event = 'refused'
else:
    storage.close()
    event = 'opened'
origins = {}
for name, module in tuple(sys.modules.items()):
    if name == 'pipy_harness' or name.startswith('pipy_harness.'):
        file = getattr(module, '__file__', None)
        if file:
            path = Path(file).resolve()
            origins[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
say(event, origins=origins, cwd=os.getcwd(), argv=sys.argv, executable=sys.executable, pgid=os.getpgid(0), runtime=sys.version, recursion_limit=sys.getrecursionlimit(), int_max_str_digits=sys.get_int_max_str_digits())
"""


def opener(path, root, label, expected):
    """An actual source-first fresh process and its complete retained evidence."""
    import hashlib
    import json

    from sqlite_child_support import Child, group_exists

    source = Path(__file__).resolve().parents[2] / "src"
    with Child(OPENER, str(path), home=root) as child:
        observed = child.expect(expected)
        assert child.wait() == 0
        child.wait_stdout_eof()
        evidence = child.evidence()
    evidence.update(
        argv=child.process.args,
        reaped=child.process.returncode is not None,
        scoped_group_absent=not group_exists(child.group),
    )
    assert observed["origins"]
    for item in observed["origins"].values():
        file = Path(item["path"])
        assert file.is_relative_to(source)
        assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
    (root / ("migration-opener-" + label + ".json")).write_text(
        json.dumps({"observed": observed, "process": evidence}, indent=2)
    )


MIGRATION_CUT_CHILD = r"""
import hashlib, threading
from pathlib import Path
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from task_migration_support import definition
path, cut = sys.argv[1:]
session = Session(open_sqlite_storage(path, mode='create'))
conversation = session.commit(lambda tx: tx.create_conversation({'kind':'ownerless'})).id
task_id = session.commit(lambda tx: tx.create_task(definition(), {'old':[1]}, ownership={'kind':'conversation'}, conversation_id=conversation))
def seed(tx):
    row = tx.task(task_id).to_tree()
    tx.set_task({**row, 'restarts':2, 'memos':{'key':'retained'}})
session.commit(seed)
session.close(timeout=5)
session = Session(open_sqlite_storage(path, mode='open'))
calls = []
def migrate(value, checkpoint, version):
    calls.append(version)
    return {'input':{'new':value}, 'checkpoint':checkpoint}
effects = Path(path + '.effects')
async def phase(task, runtime, context):
    with effects.open('a') as file:
        file.write('child-effect\n')
        file.flush()
        os.fsync(file.fileno())
    await runtime.commit(lambda tx,current: {'status':'terminal','outcome':{'status':'completed','result':'unexpected-before-cut'}})
owner = TaskScheduler(session, [definition(version=2, migrate=migrate, phase=phase)])
owner.open()
def hook(connection, point):
    row = sqlite._decode(connection.execute('SELECT record FROM tasks WHERE id=?',(task_id,)).fetchone()[0])
    if point != cut or row['version'] != 2 or row['state']['status'] != 'running':
        return
    origins = {}
    for name,module in tuple(sys.modules.items()):
        file = getattr(module,'__file__',None)
        if file and (name == 'pipy_harness' or name.startswith('pipy_harness.')):
            p = Path(file).resolve()
            origins[name] = {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    say('cut', point=point, task_id=task_id, migration_calls=calls, origins=origins, cwd=os.getcwd(), argv=sys.argv, executable=sys.executable, pgid=os.getpgid(0), runtime=sys.version, recursion_limit=sys.getrecursionlimit(), int_max_str_digits=sys.get_int_max_str_digits())
    if not threading.Event().wait(30):
        raise RuntimeError('SIGKILL boundary not collected')
sqlite._before_commit = lambda connection: hook(connection,'before')
sqlite._after_commit = lambda connection: hook(connection,'after')
say('ready', task_id=task_id)
assert command() == 'go'
owner.resume()
if not threading.Event().wait(30):
    raise RuntimeError('reservation cut not reached')
"""


def terminal_tool_watcher(target, waiting, parent):
    """Observe real terminal tool publications; do not alter any candidate."""

    def publication(value):
        for change in value.changes:
            row = change.to_tree().get("value", {})
            if row.get("kind") == "pi.tool" and row["state"]["status"] == "terminal":
                target.add(row["id"])
            if (
                row.get("id") == parent
                and row.get("state", {}).get("status") == "waiting"
            ):
                waiting.append(row["state"])

    return publication


def assert_only_parent_seed(rig, before, after, parent):
    """The typed waiting control changes no actual graph/document authority."""
    if rig.backend == "sqlite":
        for table in LOGICAL_TABLES:
            if table not in ("tasks", "durable_metadata"):
                assert before[table] == after[table]
        assert before["durable_schema"] == after["durable_schema"]
        assert [row for row in before["tasks"] if row[0] != parent] == [
            row for row in after["tasks"] if row[0] != parent
        ]
        assert before["durable_metadata"][0][1] == after["durable_metadata"][0][1]
        assert after["durable_metadata"][0][2] == before["durable_metadata"][0][2] + 1
        return
    assert before.next_id == after.next_id
    assert after.next_seq == before.next_seq + 1
    for name in (
        "documents",
        "roles",
        "requests",
        "entry_seqs",
        "addresses",
        "scopes",
        "owners",
        "entries",
        "heads",
    ):
        assert getattr(before, name) == getattr(after, name)
    for name in before.tables:
        assert {
            key: value
            for key, value in before.tables[name].items()
            if name != "task" or key != parent
        } == {
            key: value
            for key, value in after.tables[name].items()
            if name != "task" or key != parent
        }
