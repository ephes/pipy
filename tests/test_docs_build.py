"""Exercise the publication boundary with harmless, isolated Zensical inputs."""

from __future__ import annotations

import runpy
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "docs.py"


def _project(root: Path) -> Path:
    (root / "docs").mkdir()
    (root / "docs/index.md").write_text("# Home\n\nPUBLIC_DOC_SENTINEL\n")
    (root / "zensical.toml").write_text(
        '[project]\nsite_name = "Docs fixture"\n'
        '[tool.pipy_docs]\nexclude_dirs = ["audit", "parity-loop/runs"]\n'
    )
    return root


def _build(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "build", "--project-dir", str(root)],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )


def _artifacts(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root / "site").as_posix(): path.read_bytes()
        for path in (root / "site").rglob("*")
        if path.is_file()
    }


def test_build_excludes_scratch_from_artifacts_and_search_but_keeps_new_docs(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    # This fixture has no git metadata: ordinary new documentation must survive.
    (root / "docs/contribution.md").write_text("# Contribution\nNEW_PUBLIC_SENTINEL\n")
    clean = _build(root)
    assert clean.returncode == 0, clean.stderr
    before = _artifacts(root)
    assert b"NEW_PUBLIC_SENTINEL" in before["search.json"]
    for reserved in ("audit", "parity-loop/runs/harmless-run"):
        directory = root / "docs" / reserved
        directory.mkdir(parents=True)
        for name in ("sentinel.md", "trace.log", "run.jsonl"):
            (directory / name).write_text("# PRIVATE_SCRATCH_SENTINEL\n[invalid-ref]\n")
        # Traversing scratch would reject this dangling link. It must be pruned.
        (directory / "unreadable-link").symlink_to(tmp_path / "missing")
    stale = root / "site/parity-loop/runs/stale.log"
    stale.parent.mkdir(parents=True)
    stale.write_text("PRIVATE_SCRATCH_SENTINEL")
    populated = _build(root)
    assert populated.returncode == 0, populated.stderr
    after = _artifacts(root)
    assert after.keys() == before.keys()
    assert after["search.json"] == before["search.json"]
    assert all(b"PRIVATE_SCRATCH_SENTINEL" not in body for body in after.values())
    assert all(not name.startswith(("audit/", "parity-loop/runs/")) for name in after)
    assert (
        (root / "docs/audit/trace.log")
        .read_text()
        .startswith("# PRIVATE_SCRATCH_SENTINEL")
    )


@pytest.mark.parametrize(
    "broken", ["[missing](absent.md)", "[bad](#absent)", "[undefined]"]
)
def test_strict_link_failures_preserve_previous_output(
    tmp_path: Path, broken: str
) -> None:
    root = _project(tmp_path)
    assert _build(root).returncode == 0
    before = _artifacts(root)
    (root / "docs/index.md").write_text(f"# Home\n\n{broken}\n")
    result = _build(root)
    assert result.returncode != 0
    assert "Warning:" in result.stderr
    assert _artifacts(root) == before


def test_build_refuses_source_and_output_symlinks(tmp_path: Path) -> None:
    root = _project(tmp_path)
    private = root / "private.txt"
    private.write_text("PRIVATE_EXTERNAL_SENTINEL")
    link = root / "docs/link.md"
    link.symlink_to(private)
    rejected = _build(root)
    assert rejected.returncode != 0
    assert "symlinks are not supported" in rejected.stderr
    link.unlink()
    external = root / "external-output"
    external.mkdir()
    (external / "keep.txt").write_text("keep")
    (root / "site").symlink_to(external, target_is_directory=True)
    rejected = _build(root)
    assert rejected.returncode != 0
    assert "Refusing to replace site" in rejected.stderr
    assert (external / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("phase", ["after_backup", "before_pending", "after_pending"])
@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
def test_output_replacement_restores_previous_site_on_failed_or_interrupted_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: type[BaseException],
) -> None:
    root = _project(tmp_path)
    output = root / "site"
    output.mkdir()
    (output / "index.html").write_text("PREVIOUS_SUCCESSFUL_SITE")
    stage = root / "stage"
    (stage / "site").mkdir(parents=True)
    (stage / "site/index.html").write_text("NEW_SITE")
    before = _artifacts(root)
    rename = Path.rename

    def interrupted(path: Path, target: Path) -> Path:
        backup_move = path == output and target.name == "previous"
        pending_move = target == output and path.name == "site"
        if phase == "before_pending" and pending_move:
            raise failure("injected publication failure")
        result = rename(path, target)
        if (phase == "after_backup" and backup_move) or (
            phase == "after_pending" and pending_move
        ):
            raise failure("injected publication failure")
        return result

    publish = runpy.run_path(str(SCRIPT))["publish"]
    monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(failure):
        publish(stage, root)
    assert _artifacts(root) == before
    assert not list(root.glob(".pipy-docs-publish-*"))


def test_interrupted_first_publication_restores_absent_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    stage = root / "stage"
    (stage / "site").mkdir(parents=True)
    (stage / "site/index.html").write_text("NEW_SITE")
    rename = Path.rename

    def interrupted(path: Path, target: Path) -> Path:
        result = rename(path, target)
        if target == root / "site":
            raise KeyboardInterrupt("injected interruption after publication")
        return result

    publish = runpy.run_path(str(SCRIPT))["publish"]
    monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(KeyboardInterrupt):
        publish(stage, root)
    assert not (root / "site").exists()


def _wait_page(url: str, text: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200 and text in response.read().decode():
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"Preview did not serve expected status/text at {url}")


@pytest.mark.parametrize("interrupt", [signal.SIGINT, signal.SIGTERM])
def test_preview_mirrors_changes_reloads_config_and_cleans_up_child(
    tmp_path: Path, interrupt: signal.Signals
) -> None:
    root = _project(tmp_path)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    address = f"127.0.0.1:{port}"
    with (tmp_path / "preview.log").open("w") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "serve",
                "--project-dir",
                str(root),
                "--dev-addr",
                address,
            ],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            _wait_page(f"http://{address}/", "PUBLIC_DOC_SENTINEL")
            scratch = root / "docs/audit"
            scratch.mkdir()
            (scratch / "sentinel.md").write_text("# PRIVATE_PREVIEW_SENTINEL\n[bad]\n")
            (root / "docs/index.md").write_text("# Home\nEDITED_PUBLIC_SENTINEL\n")
            _wait_page(f"http://{address}/", "EDITED_PUBLIC_SENTINEL")
            added = root / "docs/added.md"
            added.write_text("# Added\nADDED_PUBLIC_SENTINEL\n")
            _wait_page(f"http://{address}/added/", "ADDED_PUBLIC_SENTINEL")
            added.unlink()
            # Zensical's preview serves its not-found page with HTTP 200.
            _wait_page(f"http://{address}/added/", "404 - Not found")
            config = root / "zensical.toml"
            config.write_text(
                config.read_text().replace("Docs fixture", "Changed title")
            )
            _wait_page(f"http://{address}/", "Changed title")
            with urllib.request.urlopen(
                f"http://{address}/search.json", timeout=1
            ) as data:
                assert b"PRIVATE_PREVIEW_SENTINEL" not in data.read()
            assert not (root / "site").exists()
        finally:
            child.send_signal(interrupt)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        assert child.returncode == 130
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_canonical_build_and_preview_commands_use_filtered_boundary() -> None:
    recipes = (REPO / "justfile").read_text()
    hosting = (REPO / ".readthedocs.yaml").read_text()
    contributing = (REPO / "CONTRIBUTING.md").read_text()
    assert "python scripts/docs.py build" in recipes
    assert "python scripts/docs.py serve" in recipes
    assert "python scripts/docs.py build" in hosting
    assert "--only-group docs python scripts/docs.py build" in contributing
