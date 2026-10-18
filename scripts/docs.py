"""Build/preview Zensical from public docs only; never ingest reserved scratch."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

RESERVED = frozenset({"audit", "parity-loop/runs"})


def configuration(root: Path) -> bytes:
    content = (root / "zensical.toml").read_bytes()
    config = tomllib.loads(content.decode())
    project = config["project"]
    # Keep the publication boundary small: output replacement must never target
    # a configurable source directory or escape the checkout.
    if project.get("docs_dir", "docs") != "docs":
        raise ValueError("The docs wrapper requires docs_dir = 'docs'")
    if project.get("site_dir", "site") != "site":
        raise ValueError("The docs wrapper requires site_dir = 'site'")
    excluded = config.get("tool", {}).get("pipy_docs", {}).get("exclude_dirs")
    if (
        not isinstance(excluded, list)
        or not all(isinstance(item, str) for item in excluded)
        or set(excluded) != RESERVED
    ):
        raise ValueError("Configure the reserved audit and parity-loop/runs exclusions")
    return content


def source_files(source: Path) -> dict[Path, os.stat_result]:
    files: dict[Path, os.stat_result] = {}
    if source.is_symlink() or not source.is_dir():
        raise ValueError("docs must be a real directory")
    for directory, dirs, names in os.walk(source):
        parent = Path(directory)
        # Prune before traversing, statting children, or opening any contents.
        dirs[:] = [
            name
            for name in dirs
            if (parent / name).relative_to(source).as_posix() not in RESERVED
        ]
        for name in dirs + names:
            path = parent / name
            relative = path.relative_to(source)
            if relative.as_posix() in RESERVED:
                continue
            if path.is_symlink():
                raise ValueError(
                    f"Documentation symlinks are not supported: {relative}"
                )
            if name in names:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"Documentation must be regular files: {relative}")
                files[relative] = info
    return files


def mirror(source: Path, destination: Path) -> bool:
    files = source_files(source)
    destination.mkdir(exist_ok=True)
    deleted = False
    # Remove stale files and now-empty directories, including file/dir swaps.
    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(destination) not in files:
            path.unlink()
            deleted = True
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for relative, info in files.items():
        target = destination / relative
        if target.is_dir():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            previous = target.stat()
            if (previous.st_mtime_ns, previous.st_size) == (
                info.st_mtime_ns,
                info.st_size,
            ):
                continue
        shutil.copy2(source / relative, target)
    return deleted


def publish(stage: Path, root: Path) -> None:
    output = root / "site"
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError("Refusing to replace site: expected a generated directory")
    # Copy across filesystems before swapping the generated output. A failed
    # build/copy leaves the last successful site intact; successful replacement
    # removes stale scratch artifacts from older unfiltered builds.
    with tempfile.TemporaryDirectory(prefix=".pipy-docs-publish-", dir=root) as tmp:
        pending = Path(tmp) / "site"
        backup = Path(tmp) / "previous"
        shutil.copytree(stage / "site", pending)
        try:
            if output.exists():
                output.rename(backup)
            pending.rename(output)
        except BaseException:  # noqa: BLE001 - restore output before propagating interruptions
            if backup.exists():
                if output.exists():
                    shutil.rmtree(output)
                backup.rename(output)
            elif not pending.exists() and output.exists():
                # Interrupted after the first-ever publication moved into place.
                shutil.rmtree(output)
            raise


def stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def preview(root: Path, stage: Path, content: bytes, address: str) -> int:
    command = [
        sys.executable,
        "-m",
        "zensical",
        "serve",
        "--dev-addr",
        address,
    ]
    child = subprocess.Popen(command, cwd=stage)
    try:
        while True:
            try:
                return child.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            deleted = mirror(root / "docs", stage / "docs")
            updated = configuration(root)
            if deleted or updated != content:
                stop(child)
                (stage / "zensical.toml").write_bytes(updated)
                # Native preview leaves deleted pages on disk. Restart from
                # fresh generated state so removed pages/search entries vanish.
                for generated in ("site", ".cache"):
                    path = stage / generated
                    if path.exists():
                        shutil.rmtree(path)
                content = updated
                child = subprocess.Popen(command, cwd=stage)
    finally:
        stop(child)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "serve"))
    parser.add_argument(
        "--project-dir", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--dev-addr", default="localhost:8000")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    root = args.project_dir.resolve()
    try:
        content = configuration(root)
        with tempfile.TemporaryDirectory(prefix="pipy-docs-") as tmp:
            stage = Path(tmp)
            (stage / "zensical.toml").write_bytes(content)
            mirror(root / "docs", stage / "docs")
            if args.command == "serve":
                return preview(root, stage, content, args.dev_addr)
            result = subprocess.run(
                [sys.executable, "-m", "zensical", "build", "--strict"],
                cwd=stage,
                check=False,
            )
            if result.returncode == 0:
                publish(stage, root)
            return result.returncode
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError) as exc:
        print(f"Documentation build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
