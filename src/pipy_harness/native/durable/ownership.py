"""Private path ownership for the durable SQLite store (DE03a); not exported.

One canonical database path has one owner process from opening through closing
(plan §3.1). The owner holds an exclusive ``flock`` on a never-deleted
``<database>.lock`` sidecar. The lock is not taken on the database file itself:
on macOS a ``flock`` there makes SQLite's own ``fcntl`` locking report
"database is locked". Lock contents are diagnostics only and never decide
ownership; a crashed owner's lock is released by the kernel, and nothing is
ever stolen by age.

Aliases meet the same lock: the parent directory is resolved with ``realpath``
and the file name kept, so symlinked or relative directories and ``..`` reach
one directory, and case variants on a case-insensitive volume reach the same
lock inode. The final database name must not be a symbolic link, and a
database, sidecar or lock file with a second hard link is refused, since its
other names would carry different lock and journal names. Within one process, a
registry of lock inodes and claimed database inodes refuses a second owner
before ``flock`` is consulted. Every descriptor is close-on-exec. In a forked
child, the ``register_at_fork`` hook closes the inherited lock and parked
descriptor copies, poisons every inherited owner and pins inherited SQLite
connections so they are never finalized there: a child finalizing an inherited
WAL connection after its owner's clean close deletes a later owner's crash-left
WAL by name (probed on macOS and Linux), losing committed rows.

Owner-only files: missing parent directories are created ``0o700``; the
database is created ``O_EXCL`` and set to ``0o600`` whatever the umask, and
SQLite gives its ``-wal``, ``-shm`` and ``-journal`` the database's mode.
Existing files with group or other bits are set to ``0o600`` when they are
regular, owned by this user, readable and writable by it and have one link;
anything else is refused (Root decision D5). Shared and network filesystems are
refused before any filesystem effect (D8): macOS requires ``MNT_LOCAL`` on APFS
or HFS+, Linux a local filesystem type from the mount table entry of the
directory's own mount. ``create`` and ``open`` are explicit (D9): ``create``
refuses an existing database or stale SQLite sidecar (SQLite would silently
delete stale sidecars next to a new empty database), ``open`` refuses a missing
database, and invalid input or an ``open`` of a missing path changes nothing on
disk.

POSIX record locks, an invariant for every DE03 slice: closing any descriptor
of a file releases every POSIX record lock this process holds on it, SQLite's
included, whichever descriptor took them. So this module never opens a
descriptor of a store file another owner in this process holds: a second open
whose lock inode or database inode (by ``lstat``) is owned here is refused
before any descriptor on a store file is opened. Every owner claims its
database inode in the registry before it connects, and a claim never moves to
another inode. As belt and braces, every descriptor on an existing store file
is opened under the registry lock and identified by ``fstat`` first; one that
reached a database another owner here claimed is parked on that owner and
closed only in the registry lock hold that drops that claim, after the owner's
connection is closed. ``flock`` belongs to one open file description, so
closing another descriptor of the lock file never releases it (Linux emulates
``flock`` with POSIX locks only on NFS, which is refused). A second SQLite
connection to an owned store from the owning process is outside the contract.

Contract boundary. The store directory is private to the owning user (owned by
that user, mode ``0700``; missing directories are created that way, and keeping
an existing one private is the caller's part). While any owner holds a store,
from the moment an open or create of it starts until its close has completed,
renaming, replacing, hard-linking or deleting any store file (the database, its
``-wal``, ``-shm`` and ``-journal``, and the lock file), by any actor, is
outside the contract. pipy detects some of these cases on a best-effort basis
(the identity checks under the lock, after connecting and after configuration;
the parking of descriptors that reach a claimed database; the re-check before
each unlink of a failed create) but guarantees nothing about them. SQLite's own
guidance is the same: do not rename, unlink or replace an open database or its
journal. With the store files left in place, the guarantees hold: one owner per
store across processes and within the process, owner-only file modes, no
descriptor of a store file held by another owner in this process is ever closed
by pipy, and invalid input, an ``open`` of a missing path and a store refused
before its lock is taken have no filesystem effect. Pi durable 1.3.0 has no
cross-process locking at all, so this remains strictly stronger than the
reference.

Pi durable 1.3.0 has no path lock, file modes or filesystem checks
(``packages/durable/README.md``: "One process owns a storage at a time; there
is no cross-process locking"); this module is pipy's own.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import functools
import importlib.metadata
import json
import os
import stat
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .storage import StorageClosedError

OpenMode = Literal["create", "open"]
# Checks an existing database through a read-only descriptor before any
# connection exists, given the size of its -wal by lstat (0 when there is none),
# raising to refuse it; the descriptor is not kept.
ExistingCheck = Callable[[int, str, int], None]

LOCK_SUFFIX = ".lock"
SIDECARS = ("-wal", "-shm", "-journal")
FILE_MODE = 0o600
DIRECTORY_MODE = 0o700

# Longest sidecar suffix; NAME_MAX is 255 bytes on the supported filesystems.
_NAME_MAX = 255
_LONGEST_SUFFIX = max(len(suffix) for suffix in (LOCK_SUFFIX, *SIDECARS))
_DIAGNOSTIC_LIMIT = 4096
# A variable, not a literal sys.platform check, so both branches type-check.
_PLATFORM = sys.platform
_DARWIN_MNT_LOCAL = 0x1000
_DARWIN_LOCAL_TYPES = frozenset({"apfs", "hfs"})
_LINUX_LOCAL_TYPES = frozenset(
    {
        "bcachefs",
        "btrfs",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "jfs",
        "nilfs2",
        "reiserfs",
        "tmpfs",
        "xfs",
        "zfs",
    }
)
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class StoragePathError(ValueError):
    """The path cannot hold a private durable store."""


class StorageNotFoundError(StoragePathError):
    """``open`` found no database at the path."""


class StorageExistsError(StoragePathError):
    """``create`` found an existing database or SQLite sidecar."""


class UnsupportedStoragePath(StoragePathError):
    """A file or filesystem at the path cannot be owned privately."""


class StorageOwnershipError(RuntimeError):
    """Another owner holds the path: another process, or this one via any alias."""


class StorageForkedError(RuntimeError):
    """An owner inherited through fork() cannot be used in the child."""


@dataclass(frozen=True, slots=True)
class StoragePath:
    """Canonical directory (no symbolic links) and the database file name."""

    directory: str
    name: str

    @property
    def database(self) -> str:
        return os.path.join(self.directory, self.name)

    @property
    def lock(self) -> str:
        return self.database + LOCK_SUFFIX

    def sidecar(self, suffix: str) -> str:
        return self.database + suffix


@dataclass(frozen=True, slots=True)
class StoreRequest:
    """Admitted caller input: exact built-in values only, nothing resolved yet."""

    head: str
    name: str
    mode: OpenMode


@dataclass(eq=False, slots=True)
class _Held:
    """Registry record of one held lock; mutated only under ``_REGISTRY_LOCK``."""

    key: tuple[int, int]
    fd: int
    pid: int
    # The owned database inode, claimed before this owner connects.
    database: tuple[int, int] | None = None
    # Descriptors of that inode a refused open reached; closing them before
    # this owner's connection is closed would drop its SQLite locks.
    parked: list[int] = field(default_factory=list)
    # Objects a forked child must never finalize (inherited SQLite connections).
    pinned: list[object] = field(default_factory=list)


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[tuple[int, int], _Held] = {}


def _owned_here(lock: tuple[int, int] | None, database: tuple[int, int] | None) -> bool:
    """Whether this process already owns the lock inode or the database inode.

    The caller holds ``_REGISTRY_LOCK``. A database inode owned here may carry
    SQLite's POSIX record locks, which closing any descriptor of it releases.
    """

    if lock is not None and lock in _REGISTRY:
        return True
    return database is not None and any(
        held.database == database for held in _REGISTRY.values()
    )


def admit_request(path: object, mode: object) -> StoreRequest:
    """Admit exact built-in input before any filesystem access.

    A ``str`` subclass or a path object could run caller code while it is
    converted or compared, or yield a different spelling later.
    """

    if type(path) is not str:
        raise TypeError("A durable store path must be an exact str")
    if type(mode) is not str or mode not in ("create", "open"):
        raise ValueError("The open mode must be exactly 'create' or 'open'")
    if not path or "\x00" in path:
        raise StoragePathError("A durable store path must be non-empty without NUL")
    try:
        os.fsencode(path)
    except UnicodeError as error:
        raise StoragePathError(
            "A durable store path must be a filesystem name"
        ) from error
    head, name = os.path.split(path)
    if name in ("", ".", ".."):
        raise StoragePathError("A durable store path must end in a database file name")
    if name.lower().endswith((LOCK_SUFFIX, *SIDECARS)):
        # Never open another store's lock or SQLite sidecar as a database.
        raise StoragePathError(
            "A database file name cannot end in a lock or SQLite sidecar suffix"
        )
    if len(os.fsencode(name)) + _LONGEST_SUFFIX > _NAME_MAX:
        raise StoragePathError(
            "The database file name leaves no room for its sidecar file names"
        )
    return StoreRequest(head, name, "create" if mode == "create" else "open")


@dataclass(frozen=True, slots=True)
class _Plan:
    path: StoragePath
    existing: str
    missing: tuple[str, ...]


def _resolve(request: StoreRequest) -> _Plan:
    """Canonical directory: ``realpath`` of the deepest existing ancestor.

    Only ``create`` may continue below missing directories, and only through
    plain names; ``x/..`` below a missing ``x`` has no meaning to resolve.
    """

    head = request.head
    if os.path.isabs(head):
        probe = head
    else:
        probe = os.path.join(os.getcwd(), head) if head else os.getcwd()
    missing: list[str] = []
    while True:
        try:
            existing = os.path.realpath(probe, strict=True)
            break
        except FileNotFoundError:
            if request.mode == "open":
                raise StorageNotFoundError(
                    f"No durable store at {os.path.join(head, request.name)!r}: "
                    "its directory is missing"
                ) from None
            parent, part = os.path.split(probe)
            if part in ("", ".", "..") or parent == probe:
                raise StoragePathError(
                    "A missing directory cannot be followed by '.' or '..'"
                ) from None
            missing.append(part)
            probe = parent
        except NotADirectoryError:
            raise StoragePathError(
                "A parent path component is not a directory"
            ) from None
    if not os.path.isdir(existing):
        raise StoragePathError("A parent path component is not a directory")
    missing.reverse()
    directory = os.path.join(existing, *missing)
    return _Plan(StoragePath(directory, request.name), existing, tuple(missing))


def _lstat(path: str, dir_fd: int | None = None) -> os.stat_result | None:
    try:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _refuse_unusable(info: os.stat_result, label: str, *, by_name: bool = True) -> None:
    """Refuse a file that cannot be owned privately.

    ``by_name`` checks a file found by name, which this process must be able to
    open read-write. A file already open here (one it just created under a
    restrictive umask, say) only needs ``fchmod``.
    """

    if stat.S_ISLNK(info.st_mode):
        raise UnsupportedStoragePath(f"The {label} is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise UnsupportedStoragePath(f"The {label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise UnsupportedStoragePath(f"The {label} belongs to another user")
    if info.st_nlink != 1:
        raise UnsupportedStoragePath(f"The {label} has {info.st_nlink} hard links")
    if by_name and info.st_mode & FILE_MODE != FILE_MODE:
        raise UnsupportedStoragePath(
            f"The {label} is not readable and writable by its owner"
        )


def _refuse_journal(path: StoragePath) -> None:
    # A durable store is WAL-only, so a rollback journal belongs to a crashed
    # foreign writer or an interrupted switch to WAL. SQLite would roll it back
    # into the database on its first read; refuse before anything opens it.
    raise UnsupportedStoragePath(
        f"The rollback journal {path.sidecar('-journal')!r} exists; the database"
        " may need recovery outside the durable store and is left untouched"
    )


def _park_if_claimed(
    fd: int, info: os.stat_result, held: _Held | None, label: str
) -> None:
    """Refuse a descriptor that reached a database another owner here claimed.

    The caller holds ``_REGISTRY_LOCK``. Closing the descriptor would drop that
    owner's SQLite locks, so it is parked on the owner and closed only when the
    owner releases, after its connection is closed. Claims never move, so a
    parked descriptor always references its owner's claimed inode.
    """

    key = (info.st_dev, info.st_ino)
    for other in _REGISTRY.values():
        if other is not held and other.database == key:
            other.parked.append(fd)
            raise StorageOwnershipError(f"The {label} is already open in this process")


def _open_unowned(
    name: str, dir_fd: int, info: os.stat_result, label: str, held: _Held | None
) -> int:
    """Open the existing store file ``info`` describes, read-only.

    The caller holds the registry lock until it closes the descriptor, so no
    owner can claim the inode and connect in between. The descriptor is
    identified before anything else: a racing rename may have put another
    owner's database under ``name`` since it was checked.
    """

    fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    now = os.fstat(fd)  # On failure the descriptor is left open, never closed.
    _park_if_claimed(fd, now, held, label)
    if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
        os.close(fd)
        raise UnsupportedStoragePath(f"The {label} changed while it was checked")
    return fd


def _check_existing(
    name: str,
    dir_fd: int,
    info: os.stat_result,
    check: ExistingCheck,
    label: str,
    wal: os.stat_result | None,
    held: _Held | None = None,
) -> None:
    """Run ``check`` on a read-only descriptor of the file ``info`` describes."""

    fd = _open_unowned(name, dir_fd, info, label, held)
    try:
        check(fd, label, 0 if wal is None else wal.st_size)
    finally:
        os.close(fd)


def _refuse_sidecars(
    path: StoragePath, sidecars: dict[str, os.stat_result | None]
) -> None:
    for suffix, sidecar in sidecars.items():
        if sidecar is not None:
            _refuse_unusable(sidecar, f"SQLite file {path.sidecar(suffix)!r}")
            if suffix == "-journal":
                _refuse_journal(path)


def _precheck_existing(path: StoragePath) -> None:
    database = _lstat(path.database)
    if database is None:
        raise StorageNotFoundError(f"No durable store at {path.database!r}")
    _refuse_unusable(database, f"database {path.database!r}")
    _refuse_sidecars(
        path, {suffix: _lstat(path.sidecar(suffix)) for suffix in SIDECARS}
    )


def _precheck(plan: _Plan, mode: OpenMode) -> None:
    """Refusals by ``lstat`` alone: no effect, and no descriptor on the store."""

    if plan.missing:
        return
    path = plan.path
    if mode == "create":
        for suffix in ("", *SIDECARS):
            if _lstat(path.sidecar(suffix)) is not None:
                raise StorageExistsError(
                    f"Cannot create {path.database!r}: {path.name + suffix!r} exists"
                )
    else:
        _precheck_existing(path)
    lock = _lstat(path.lock)
    if lock is not None:
        _refuse_unusable(lock, f"lock file {path.lock!r}")


# Filesystem classification ----------------------------------------------------


class _DarwinStatfs(ctypes.Structure):
    """``struct statfs`` with 64-bit inodes (sys/mount.h, MFSTYPENAMELEN 16)."""

    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def darwin_verdict(fstypename: str, flags: int) -> str | None:
    """Refusal reason for a macOS mount, or ``None`` when it is supported."""

    if not flags & _DARWIN_MNT_LOCAL:
        return f"{fstypename or 'unknown'} is not a local filesystem"
    if fstypename not in _DARWIN_LOCAL_TYPES:
        return f"{fstypename or 'unknown'} is not a supported local filesystem"
    return None


@functools.cache
def _darwin_fstatfs() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        # x86_64 exports the 64-bit-inode layout under this name; arm64 only
        # has the plain symbol, which already uses it.
        fstatfs = libc["fstatfs$INODE64"]
    except AttributeError:
        fstatfs = libc["fstatfs"]
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(_DarwinStatfs)]
    fstatfs.restype = ctypes.c_int
    return fstatfs


def _darwin_filesystem(fd: int) -> tuple[str, int]:
    result = _DarwinStatfs()
    if _darwin_fstatfs()(fd, ctypes.byref(result)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result.f_fstypename.decode("utf-8", "replace"), int(result.f_flags)


def linux_mount_type(fdinfo: str, mountinfo: str) -> str:
    """The type of the mount an open descriptor lives on.

    The descriptor's ``mnt_id`` selects one mount table line exactly, so bind
    mounts, overmounts and escaped mount points need no path matching.
    """

    mount = next(
        (
            line.split()[1]
            for line in fdinfo.splitlines()
            if line.startswith("mnt_id:") and len(line.split()) == 2
        ),
        None,
    )
    if mount is None:
        raise UnsupportedStoragePath("The kernel does not report the directory's mount")
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if separator and fields and fields[0] == mount and after.split():
            return after.split()[0]
    raise UnsupportedStoragePath(
        "The directory's mount is missing from the mount table"
    )


def linux_verdict(fstype: str) -> str | None:
    """Refusal reason for a Linux filesystem type, or ``None`` when supported."""

    if fstype in _LINUX_LOCAL_TYPES:
        return None
    return f"{fstype} is not a supported local filesystem"


def _linux_filesystem(fd: int) -> str:
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="utf-8") as stream:
            fdinfo = stream.read()
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            mountinfo = stream.read()
    except OSError as error:
        raise UnsupportedStoragePath(
            "Cannot classify the filesystem without /proc mount information"
        ) from error
    return linux_mount_type(fdinfo, mountinfo)


def classify_directory(fd: int) -> None:
    """Refuse shared, network and unclassifiable filesystems (Root decision D8)."""

    if _PLATFORM == "darwin":
        fstype, flags = _darwin_filesystem(fd)
        reason = darwin_verdict(fstype, flags)
    elif _PLATFORM.startswith("linux"):
        reason = linux_verdict(_linux_filesystem(fd))
    else:
        reason = f"durable stores are not supported on {_PLATFORM}"
    if reason is not None:
        raise UnsupportedStoragePath(
            f"Unsupported filesystem for a durable store: {reason}"
        )


# Directories, lock and database -----------------------------------------------


def _sync_directory(fd: int) -> None:
    """Make new directory entries durable; on macOS only F_FULLFSYNC flushes."""

    os.fsync(fd)
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if _PLATFORM == "darwin" and full is not None:
        fcntl.fcntl(fd, full)


def _open_directory(plan: _Plan) -> int:
    """Classify the existing ancestor, then create missing directories 0o700."""

    fd = os.open(plan.existing, _DIRECTORY_FLAGS)
    try:
        classify_directory(fd)
        for part in plan.missing:
            fd = _make_directory(fd, part)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _make_directory(parent: int, part: str) -> int:
    try:
        os.mkdir(part, DIRECTORY_MODE, dir_fd=parent)
        created = True
    except FileExistsError:
        created = False
    if created:
        # The umask may have removed owner bits; the name is ours to fix.
        os.chmod(part, DIRECTORY_MODE, dir_fd=parent)
    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=parent)
    try:
        info = os.fstat(child)
        if info.st_uid != os.geteuid():
            raise UnsupportedStoragePath(
                f"The directory {part!r} belongs to another user"
            )
        if created:
            _sync_directory(parent)
    except BaseException:
        os.close(child)
        raise
    os.close(parent)
    return child


def _private_fd(fd: int, label: str) -> os.stat_result:
    """Check an open file and make it exactly owner read/write."""

    info = os.fstat(fd)
    _refuse_unusable(info, label, by_name=False)
    if stat.S_IMODE(info.st_mode) != FILE_MODE:
        os.fchmod(fd, FILE_MODE)
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != FILE_MODE:
            raise UnsupportedStoragePath(
                f"The filesystem cannot keep the {label} owner-only"
            )
    return info


@functools.cache
def _pipy_version() -> str:
    try:
        return importlib.metadata.version("pipy")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _diagnostic() -> bytes:
    value = {
        "schema": 1,
        "pid": os.getpid(),
        "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipy": _pipy_version(),
    }
    return (json.dumps(value, sort_keys=True) + "\n").encode("ascii")


def _holder(fd: int) -> str:
    """Bounded diagnostic text from a held lock; it never decides anything."""

    try:
        value = json.loads(os.pread(fd, _DIAGNOSTIC_LIMIT, 0).decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return ""
    pid = value.get("pid") if type(value) is dict else None
    return f" (lock diagnostic: pid {pid})" if type(pid) is int and pid > 0 else ""


def _gate_existing(dir_fd: int, path: StoragePath, check: ExistingCheck) -> None:
    """Check an existing database's format before its lock is taken.

    Under the registry lock, and only once neither its lock inode nor its
    database inode is owned by this process: owners claim both before they
    connect, so closing the read-only descriptor cannot release an owner's
    SQLite locks (see the module docstring on POSIX record locks).
    """

    label = f"database {path.database!r}"
    with _REGISTRY_LOCK:
        database = _lstat(path.name, dir_fd)
        if database is None:
            raise StorageNotFoundError(f"No durable store at {path.database!r}")
        _refuse_unusable(database, label)
        lock = _lstat(path.name + LOCK_SUFFIX, dir_fd)
        lock_key = None if lock is None else (lock.st_dev, lock.st_ino)
        if _owned_here(lock_key, (database.st_dev, database.st_ino)):
            raise StorageOwnershipError(
                f"The durable store {path.database!r} is already open in this process"
            )
        # By lstat only. Its size can only defer a decision to the checks under
        # the lock, which refuse a linked or foreign -wal before anything uses it.
        wal = _lstat(path.name + "-wal", dir_fd)
        _check_existing(path.name, dir_fd, database, check, label, wal)


def _claim_database(held: _Held, info: os.stat_result, path: StoragePath) -> None:
    """Claim this owner's database inode before it is opened or connected.

    The caller holds ``_REGISTRY_LOCK``. A claim is never moved to another
    inode: descriptors parked on this owner reference its claimed inode and
    stay covered by the claim until the owner releases. A name that now names
    another inode fails the open instead.
    """

    key = (info.st_dev, info.st_ino)
    if any(other is not held and other.database == key for other in _REGISTRY.values()):
        raise StorageOwnershipError(
            "The durable store's database is already open in this process"
        )
    if held.database is not None and held.database != key:
        raise UnsupportedStoragePath(
            f"The durable store {path.database!r} changed while opening"
        )
    held.database = key


def _after_lock() -> None:
    """Test seam between taking the flock and re-checking the lock file name."""


def _lock(dir_fd: int, path: StoragePath) -> _Held:
    name = path.name + LOCK_SUFFIX
    label = f"lock file {path.lock!r}"
    # Under the registry lock, which is also held across fork(), no lock
    # descriptor exists in this process without its registry record.
    with _REGISTRY_LOCK:
        try:
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                FILE_MODE,
                dir_fd=dir_fd,
            )
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EACCES):
                raise UnsupportedStoragePath(f"Cannot open the {label}") from error
            raise
        # Identified first, like every store descriptor: a rename may have put
        # another owner's database under the lock name.
        _park_if_claimed(fd, os.fstat(fd), None, label)
        try:
            info = _private_fd(fd, label)
            key = (info.st_dev, info.st_ino)
            database = _lstat(path.name, dir_fd)
            database_key = (
                None if database is None else (database.st_dev, database.st_ino)
            )
            if _owned_here(key, database_key):
                raise StorageOwnershipError(
                    f"The durable store {path.database!r} is already open in this process"
                )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StorageOwnershipError(
                    f"The durable store {path.database!r} is owned by another process"
                    + _holder(fd)
                ) from None
            except OSError as error:
                raise UnsupportedStoragePath(
                    f"The filesystem refuses an exclusive lock on the {label}"
                ) from error
            _after_lock()
            current = _lstat(name, dir_fd)
            if current is None or (current.st_dev, current.st_ino) != key:
                raise StorageOwnershipError(
                    f"The {label} was replaced while it was being locked"
                )
            try:
                os.ftruncate(fd, 0)
                os.pwrite(fd, _diagnostic(), 0)
            except OSError:
                pass  # Diagnostic only: the held flock alone is the ownership.
            held = _Held(key, fd, os.getpid(), database_key)
            _REGISTRY[key] = held
            return held
        except BaseException:
            os.close(fd)
            raise


def _unlock(held: _Held) -> None:
    """Release a lock; the owner's connection, if any, is already closed."""

    with _REGISTRY_LOCK:
        if _REGISTRY.get(held.key) is held:
            del _REGISTRY[held.key]
        try:
            # LOCK_UN releases even if a stray inherited copy of this open file
            # description survives in a child that skipped the fork hooks.
            fcntl.flock(held.fd, fcntl.LOCK_UN)
        finally:
            os.close(held.fd)
            # Still under the registry lock, so nobody claims and connects first.
            while held.parked:
                os.close(held.parked.pop())


def _normalize(
    dir_fd: int, name: str, info: os.stat_result, label: str, held: _Held
) -> os.stat_result:
    _refuse_unusable(info, label)
    if stat.S_IMODE(info.st_mode) == FILE_MODE:
        return info
    fd = _open_unowned(name, dir_fd, info, label, held)
    try:
        return _private_fd(fd, label)
    finally:
        os.close(fd)


def _create_database(dir_fd: int, path: StoragePath, held: _Held) -> os.stat_result:
    for suffix in ("", *SIDECARS):
        if _lstat(path.name + suffix, dir_fd) is not None:
            raise StorageExistsError(
                f"Cannot create {path.database!r}: {path.name + suffix!r} exists"
            )
    try:
        fd = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            FILE_MODE,
            dir_fd=dir_fd,
        )
    except FileExistsError:
        raise StorageExistsError(
            f"Cannot create {path.database!r}: it exists"
        ) from None
    made: tuple[int, int] | None = None
    try:
        try:
            first = os.fstat(fd)
            made = (first.st_dev, first.st_ino)
            with _REGISTRY_LOCK:  # A new inode: nobody here can hold locks on it.
                _claim_database(held, first, path)
            info = _private_fd(fd, f"database {path.database!r}")
        finally:
            os.close(fd)
        # Inside the cleanup scope: a create whose new entry cannot be made
        # durable leaves nothing behind and can simply be retried.
        _sync_directory(dir_fd)
    except BaseException as error:
        if made is not None:
            _unlink_created(dir_fd, path.name, made, error)
        raise
    return info


def _unlink_created(
    dir_fd: int, name: str, identity: tuple[int, int], error: BaseException
) -> None:
    """Remove exactly the file this create made; keep ``error`` the cause."""

    try:
        current = _lstat(name, dir_fd)
        if (
            current is not None
            and stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            os.unlink(name, dir_fd=dir_fd)
    except OSError as cleanup:
        error.add_note(f"Removing the new database also failed: {cleanup!r}")


def _open_database(
    dir_fd: int, path: StoragePath, check: ExistingCheck | None, held: _Held
) -> tuple[os.stat_result, int]:
    """Re-check an existing database under the lock, then make it owner-only.

    The database inode is claimed before any descriptor opens it, and this
    owner has not connected yet; every descriptor is opened and closed under
    one registry lock hold (see the module docstring on POSIX record locks).
    """

    database = _lstat(path.name, dir_fd)
    if database is None:
        raise StorageNotFoundError(f"No durable store at {path.database!r}")
    label = f"database {path.database!r}"
    _refuse_unusable(database, label)
    sidecars = {suffix: _lstat(path.name + suffix, dir_fd) for suffix in SIDECARS}
    _refuse_sidecars(path, sidecars)
    with _REGISTRY_LOCK:
        _claim_database(held, database, path)
        if check is not None:
            # Before any mode change, so a refused file keeps its bytes and mode.
            wal = sidecars["-wal"]
            _check_existing(path.name, dir_fd, database, check, label, wal, held)
        info = _normalize(dir_fd, path.name, database, label, held)
        wal_size = 0
        for suffix, sidecar in sidecars.items():
            if sidecar is not None:
                label = f"SQLite file {path.sidecar(suffix)!r}"
                sidecar = _normalize(dir_fd, path.name + suffix, sidecar, label, held)
                if suffix == "-wal":
                    wal_size = sidecar.st_size
    return info, wal_size


class PathOwner:
    """The exclusive owner of one canonical durable store path.

    ``close()`` is idempotent. In a process forked from the owner, every
    operation raises ``StorageForkedError`` and ``close()`` does nothing: the
    fork hook already closed the child's copy of the lock, and the child owns
    nothing.
    """

    __slots__ = (
        "path",
        "created",
        "database_identity",
        "directory_identity",
        "wal_size_at_open",
        "_held",
        "_lock",
        "_closed",
    )

    def __init__(
        self,
        path: StoragePath,
        held: _Held,
        *,
        created: bool,
        database: os.stat_result,
        directory: os.stat_result,
        wal_size: int,
    ) -> None:
        self.path = path
        self.created = created
        self.database_identity = (database.st_dev, database.st_ino)
        self.directory_identity = (directory.st_dev, directory.st_ino)
        # Bytes of a -wal file found under the lock before any connection:
        # non-zero means the previous owner did not close cleanly (D6 input).
        self.wal_size_at_open = wal_size
        self._held = held
        self._lock = threading.Lock()
        self._closed = False

    @property
    def forked(self) -> bool:
        return self._held.pid != os.getpid()

    @property
    def lock_fd(self) -> int:
        return self._held.fd

    def check_usable(self) -> None:
        if self.forked:
            raise StorageForkedError(
                "A durable store owner cannot be used after fork()"
            )
        with self._lock:
            if self._closed:
                raise StorageClosedError("The durable store owner is closed")

    def pin_in_forked_children(self, value: object) -> None:
        """Keep ``value`` from ever being finalized in a forked child."""

        with _REGISTRY_LOCK:
            self._held.pinned.append(value)

    def discard_created_files(self) -> None:
        """After a failed ``create``, remove the database and sidecars it made.

        Only files this owner created exist under its lock (``create`` refused
        any existing database or sidecar). The lock file and any created
        directories stay.
        """

        self.check_usable()
        if not self.created:
            return
        fd = os.open(self.path.directory, _DIRECTORY_FLAGS)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self.directory_identity:
                raise UnsupportedStoragePath("The store directory changed while open")
            for suffix in SIDECARS:
                sidecar = _lstat(self.path.name + suffix, fd)
                if sidecar is not None and stat.S_ISREG(sidecar.st_mode):
                    self._check_created(fd)
                    os.unlink(self.path.name + suffix, dir_fd=fd)
            self._check_created(fd)
            os.unlink(self.path.name, dir_fd=fd)
            _sync_directory(fd)
        finally:
            os.close(fd)

    def _check_created(self, fd: int) -> None:
        """Right before an unlink by name: the name still names this create's file.

        Best-effort (see the contract boundary): a replacement that arrives
        between this check and the unlink is not seen.
        """

        database = _lstat(self.path.name, fd)
        if (
            database is None
            or (database.st_dev, database.st_ino) != self.database_identity
        ):
            raise UnsupportedStoragePath(
                f"The durable store {self.path.database!r} changed while open"
            )

    def close(self) -> None:
        if self.forked:
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True
            _unlock(self._held)


def acquire(request: StoreRequest, *, check: ExistingCheck | None = None) -> PathOwner:
    """Own the canonical path of an admitted request, creating it for ``create``.

    ``check`` validates an existing database's format (``open``) on a read-only
    descriptor twice: before the lock, so a refused file gets no lock file or
    other effect, and again under the lock before any change. Each time it also
    gets the size of the ``-wal`` by ``lstat`` (no descriptor), which tells that
    the previous owner did not close cleanly. Neither read
    happens until the in-process ownership check has excluded another owner of
    the lock or database inode (see the module docstring on POSIX locks).
    """

    plan = _resolve(request)
    _precheck(plan, request.mode)
    dir_fd = _open_directory(plan)
    try:
        directory = os.fstat(dir_fd)
        if request.mode == "open" and check is not None:
            _gate_existing(dir_fd, plan.path, check)
        held = _lock(dir_fd, plan.path)
        try:
            if request.mode == "create":
                database, wal_size = _create_database(dir_fd, plan.path, held), 0
            else:
                database, wal_size = _open_database(dir_fd, plan.path, check, held)
        except BaseException:
            _unlock(held)
            raise
    finally:
        os.close(dir_fd)
    return PathOwner(
        plan.path,
        held,
        created=request.mode == "create",
        database=database,
        directory=directory,
        wal_size=wal_size,
    )


def acquire_path(path: object, mode: object) -> PathOwner:
    """Admit, resolve and own a durable store path (see ``acquire``)."""

    return acquire(admit_request(path, mode))


# fork() ------------------------------------------------------------------------


def _pin_forever(values: list[object]) -> None:
    """Leak one reference to each value for the rest of this (child) process.

    The child never finalizes an inherited SQLite connection, which would run
    ``sqlite3_close`` against files that later owners may have replaced.
    """

    for value in values:
        ctypes.pythonapi.Py_IncRef(ctypes.py_object(value))


def _fork_prepare() -> None:
    # Held across fork() so the child never inherits a half-registered lock;
    # a with-block cannot span the before and after hooks.
    _REGISTRY_LOCK.__enter__()


def _fork_parent() -> None:
    _REGISTRY_LOCK.__exit__(None, None, None)


def _fork_child() -> None:
    global _REGISTRY_LOCK
    # The inherited lock is held by the forking thread's prepare hook.
    _REGISTRY_LOCK = threading.Lock()
    inherited = list(_REGISTRY.values())
    _REGISTRY.clear()
    for held in inherited:
        _pin_forever(held.pinned)
        # Closing (never LOCK_UN) these copies leaves the parent's locks held:
        # flock belongs to the shared description, record locks to the parent.
        for fd in (held.fd, *held.parked):
            try:
                os.close(fd)
            except OSError:
                pass


os.register_at_fork(
    before=_fork_prepare, after_in_parent=_fork_parent, after_in_child=_fork_child
)
