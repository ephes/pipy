"""Codemode runtime provisioning: the pinned CPython-on-WASI build.

The runtime is never fetched during a turn. ``install_runtime`` is the only
code that downloads or unpacks it, and it runs only from the explicit install
command (``python -m pipy_harness.native.codemode install``). The archive must
match the pinned size and sha256 before anything is unpacked.

Layout under the codemode root (default ``~/.local/state/pipy/codemode``)::

    runtime/cpython-<version>-wasi-<sha256[:16]>/
        python.wasm  lib/python3.14/...  LICENSE  manifest.json
    cache/python-<key>.cwasm + python-<key>.json   (written by the worker)

``manifest.json`` lists the sha256 of every unpacked file. It is derived from
the verified archive at install time, and :func:`verify_runtime` checks the
whole tree against it, so a partial install, a modified or extra file, or a
symlink makes codemode unavailable. It detects corruption and drift; it is not
a defence against someone who can already write the user's state directory
(they could equally edit pipy itself).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any, BinaryIO

MANIFEST_NAME = "manifest.json"
WASM_NAME = "python.wasm"
MANIFEST_SCHEMA = "pipy.codemode-runtime"
INSTALL_HINT = "python -m pipy_harness.native.codemode install"

_CHUNK = 1 << 20
_MAX_UNPACKED_BYTES = 256 * 1024 * 1024
_ALLOWED_TOP_LEVEL = frozenset({"LICENSE", WASM_NAME, "lib"})
# Zip members without Unix mode bits carry no file type (0); anything typed must
# be a regular file or a directory, so symlinks and devices are refused.
_ALLOWED_FILE_TYPES = frozenset({0, stat.S_IFREG, stat.S_IFDIR})


@dataclass(frozen=True, slots=True)
class RuntimePin:
    version: str
    url: str
    sha256: str
    size: int

    @property
    def dir_name(self) -> str:
        return f"cpython-{self.version}-wasi-{self.sha256[:16]}"


# brettcannon/cpython-wasi-build release v3.14.7 (wasi-sdk 24). The digest is
# the one GitHub reports for the asset and the one the CM1 spike ran against.
RUNTIME_PIN = RuntimePin(
    version="3.14.7",
    url=(
        "https://github.com/brettcannon/cpython-wasi-build/releases/download/"
        "v3.14.7/python-3.14.7-wasi_sdk-24.zip"
    ),
    sha256="2e064d3fb8172471d39d741348efa722349c40b96301f69968dff714999c584b",
    size=14_291_017,
)


@dataclass(frozen=True, slots=True)
class CodemodePaths:
    root: Path

    def runtime_dir(self, pin: RuntimePin = RUNTIME_PIN) -> Path:
        return self.root / "runtime" / pin.dir_name

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"


def default_paths(home: Path | None = None) -> CodemodePaths:
    base = home if home is not None else Path.home()
    return CodemodePaths(base / ".local" / "state" / "pipy" / "codemode")


class CodemodeRuntimeError(Exception):
    """Install or verification failure with a one-line, user-facing reason."""


@dataclass(frozen=True, slots=True)
class InstallResult:
    runtime_dir: Path
    status: str  # "installed" | "already-installed"


Fetcher = Callable[[str], IO[bytes]]


def _default_fetch(url: str) -> IO[bytes]:
    response: IO[bytes] = urllib.request.urlopen(url, timeout=60)  # noqa: S310 - pinned https URL
    return response


def verify_runtime(paths: CodemodePaths, pin: RuntimePin = RUNTIME_PIN) -> Path:
    """Return the verified runtime directory or raise with the reason."""

    runtime_dir = paths.runtime_dir(pin)
    if not runtime_dir.is_dir() or runtime_dir.is_symlink():
        raise CodemodeRuntimeError(
            f"the codemode runtime is not installed (run: {INSTALL_HINT})"
        )
    manifest = _read_manifest(runtime_dir)
    if manifest.get("runtime_sha256") != pin.sha256:
        raise CodemodeRuntimeError(
            f"the installed runtime does not match the pinned sha256 {pin.sha256[:12]}… "
            f"(reinstall: {INSTALL_HINT})"
        )
    expected: dict[str, str] = manifest["files"]
    try:
        actual = _tree_digests(runtime_dir)
    except OSError as exc:
        raise CodemodeRuntimeError(f"cannot read the installed runtime: {exc}") from exc
    if actual != expected:
        raise CodemodeRuntimeError(
            f"the installed runtime differs from its manifest: {_describe_drift(expected, actual)} "
            f"(reinstall: {INSTALL_HINT})"
        )
    return runtime_dir


def _read_manifest(runtime_dir: Path) -> dict[str, Any]:
    try:
        with _open_regular(runtime_dir / MANIFEST_NAME) as handle:
            body = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise CodemodeRuntimeError(
            f"the runtime manifest is missing or unreadable (reinstall: {INSTALL_HINT})"
        ) from exc
    files = body.get("files") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("schema") != MANIFEST_SCHEMA
        or not isinstance(files, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items())
    ):
        raise CodemodeRuntimeError(
            f"the runtime manifest is malformed (reinstall: {INSTALL_HINT})"
        )
    return body


def _tree_digests(runtime_dir: Path) -> dict[str, str]:
    """Hash every file under ``runtime_dir`` except the manifest.

    Symlinks and special files are reported with a sentinel digest so they can
    never match a manifest entry.
    """

    digests: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(runtime_dir, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath, name)
            relative = path.relative_to(runtime_dir).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if relative == MANIFEST_NAME:
                continue
            digests[relative] = (
                _file_sha256(path) if stat.S_ISREG(mode) else "<not a regular file>"
            )
    return digests


def _describe_drift(expected: dict[str, str], actual: dict[str, str]) -> str:
    for name in sorted(expected.keys() - actual.keys()):
        return f"{name} is missing"
    for name in sorted(actual.keys() - expected.keys()):
        return f"{name} is unexpected"
    for name in sorted(expected):
        if expected[name] != actual[name]:
            return f"{name} was modified"
    return "unknown difference"


def _open_regular(path: Path) -> BinaryIO:
    """Open ``path`` for reading only if it is a regular file, not via a symlink.

    The type is checked on the opened descriptor, so a FIFO or device swapped
    in after a directory scan can neither block nor be read.
    """

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular(path) as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_runtime(
    paths: CodemodePaths,
    *,
    source: Path | None = None,
    force: bool = False,
    pin: RuntimePin = RUNTIME_PIN,
    fetch: Fetcher = _default_fetch,
) -> InstallResult:
    """Install the pinned runtime from ``source`` (a local zip) or its URL."""

    if not force:
        try:
            return InstallResult(verify_runtime(paths, pin), "already-installed")
        except CodemodeRuntimeError:
            pass
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=paths.root, prefix=".install-"))
    try:
        archive = staging / "runtime.zip"
        if source is not None:
            with source.open("rb") as handle:
                _copy_verified(handle, archive, pin)
        else:
            with fetch(pin.url) as handle:
                _copy_verified(handle, archive, pin)
        unpacked = staging / "unpacked"
        files = _unpack(archive, unpacked)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "runtime_sha256": pin.sha256,
            "version": pin.version,
            "files": files,
        }
        (unpacked / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
        )
        target = paths.runtime_dir(pin)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _replace_dir(unpacked, target, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return InstallResult(verify_runtime(paths, pin), "installed")


def _copy_verified(handle: IO[bytes], archive: Path, pin: RuntimePin) -> None:
    digest = hashlib.sha256()
    size = 0
    with archive.open("wb") as out:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            size += len(chunk)
            if size > pin.size:
                raise CodemodeRuntimeError(
                    f"the runtime archive is larger than the pinned {pin.size} bytes"
                )
            digest.update(chunk)
            out.write(chunk)
    if size != pin.size or digest.hexdigest() != pin.sha256:
        raise CodemodeRuntimeError(
            f"the runtime archive does not match the pinned sha256 {pin.sha256} "
            f"(got {digest.hexdigest()}, {size} bytes)"
        )


def _unpack(archive: Path, destination: Path) -> dict[str, str]:
    """Unpack regular files only, refusing unsafe member names; return digests."""

    files: dict[str, str] = {}
    total = 0
    destination.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            relative = _safe_member_name(info)
            if info.is_dir():
                (destination / relative).mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            if total > _MAX_UNPACKED_BYTES:
                raise CodemodeRuntimeError(
                    "the runtime archive unpacks to more than the allowed size"
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with bundle.open(info) as member, target.open("xb") as out:
                for chunk in iter(lambda: member.read(_CHUNK), b""):
                    digest.update(chunk)
                    out.write(chunk)
            files[relative] = digest.hexdigest()
    if WASM_NAME not in files:
        raise CodemodeRuntimeError("the runtime archive has no python.wasm")
    return files


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    path = PurePosixPath(name)
    unix_mode = info.external_attr >> 16
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] not in _ALLOWED_TOP_LEVEL
        or stat.S_IFMT(unix_mode) not in _ALLOWED_FILE_TYPES
    ):
        raise CodemodeRuntimeError(
            f"the runtime archive has an unsafe member: {name!r}"
        )
    return path.as_posix()


def _replace_dir(new: Path, target: Path, staging: Path) -> None:
    """Swap ``new`` into ``target``; the old tree is moved into staging first."""

    if target.exists() or target.is_symlink():
        os.replace(target, staging / "previous")
    os.replace(new, target)
