"""Deterministic regressions for the optional workflow archive's owners."""

from __future__ import annotations

import json
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event

import pytest

import pipy_session.auto_capture as capture
import pipy_session.recorder as recorder

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def initialize(root):
    return recorder.init_session(
        root=root, agent="codex", slug="generation", machine="test", now=NOW
    )


def observe_lock(monkeypatch, module, attempted):
    original = module.file_lock

    @contextmanager
    def observed(path):
        attempted.set()
        with original(path):
            yield

    monkeypatch.setattr(module, "file_lock", observed)


def test_finalize_waits_for_admitted_append_and_late_append_cannot_recreate(
    tmp_path, monkeypatch
):
    active = initialize(tmp_path)
    writing, release, finalizing = Event(), Event(), Event()
    original = recorder._write_jsonl_event

    def paused_write(handle, event):
        writing.set()
        assert release.wait(10)
        original(handle, event)

    monkeypatch.setattr(recorder, "_write_jsonl_event", paused_write)
    with ThreadPoolExecutor(2) as pool:
        append = pool.submit(
            recorder.append_event, active, root=tmp_path, event_type="admitted"
        )
        try:
            assert writing.wait(10)
            observe_lock(monkeypatch, recorder, finalizing)
            finalize = pool.submit(recorder.finalize_session, active, root=tmp_path)
            assert finalizing.wait(10)
            assert not finalize.done()
        finally:
            release.set()
        append.result(timeout=10)
        record = finalize.result(timeout=10)
    before = record.jsonl_path.read_bytes()
    assert json.loads(before.splitlines()[-1])["type"] == "admitted"
    with pytest.raises(FileNotFoundError):
        recorder.append_event(active, root=tmp_path, event_type="late")
    assert not active.exists()
    assert record.jsonl_path.read_bytes() == before


def test_process_append_and_thread_finalize_share_owner(tmp_path, monkeypatch):
    active = initialize(tmp_path)
    script = """
import sys
from pipy_session import recorder
original = recorder._write_jsonl_event
def paused(handle, event):
    print("writing", flush=True)
    assert sys.stdin.readline().strip() == "release"
    original(handle, event)
recorder._write_jsonl_event = paused
recorder.append_event(sys.argv[1], root=sys.argv[2], event_type="process.append")
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(active), str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    attempted = Event()
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == "writing"
        observe_lock(monkeypatch, recorder, attempted)
        with ThreadPoolExecutor(1) as pool:
            finalizing = pool.submit(recorder.finalize_session, active, root=tmp_path)
            try:
                assert attempted.wait(10)
                assert not finalizing.done()
            finally:
                assert child.stdin is not None
                child.stdin.write("release\n")
                child.stdin.flush()
            record = finalizing.result(timeout=10)
        _, errors = child.communicate(timeout=10)
        assert child.returncode == 0, errors
        assert json.loads(record.jsonl_path.read_text().splitlines()[-1])["type"] == (
            "process.append"
        )
        before = record.jsonl_path.read_bytes()
        late = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pipy_session.recorder import append_event; import sys; "
                "append_event(sys.argv[1], root=sys.argv[2], event_type='late')",
                str(active),
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert late.returncode != 0
        assert "FileNotFoundError" in late.stderr
        assert not active.exists()
        assert record.jsonl_path.read_bytes() == before
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_start_after_finalize_reserves_new_generation_even_at_same_timestamp(tmp_path):
    old = initialize(tmp_path)
    record = recorder.finalize_session(old, root=tmp_path)
    replacement = initialize(tmp_path)
    assert replacement != old
    assert record.jsonl_path.exists()
    with pytest.raises(FileNotFoundError):
        recorder.append_event(old, root=tmp_path, event_type="stale")
    assert len(replacement.read_text().splitlines()) == 1


def start(root):
    return capture.start_auto_capture(
        root=root,
        agent="codex",
        slug="generation",
        platform_session_id="shared",
        machine="test",
        now=NOW,
    )


def test_stop_cannot_delete_replacement_mapping(tmp_path, monkeypatch):
    old = start(tmp_path)
    renamed, release, starting = Event(), Event(), Event()
    original = capture.finalize_session

    def paused_finalize(*args, **kwargs):
        record = original(*args, **kwargs)
        renamed.set()
        assert release.wait(10)
        return record

    monkeypatch.setattr(capture, "finalize_session", paused_finalize)
    with ThreadPoolExecutor(2) as pool:
        stop = pool.submit(
            capture.stop_auto_capture,
            root=tmp_path,
            agent="codex",
            platform_session_id="shared",
        )
        try:
            assert renamed.wait(10)
            observe_lock(monkeypatch, capture, starting)
            replacement = pool.submit(start, tmp_path)
            assert starting.wait(10)
            assert not replacement.done()
        finally:
            release.set()
        record = stop.result(timeout=10)
        new = replacement.result(timeout=10)
    assert new.active_path != old.active_path
    assert new.active_path.exists()
    assert json.loads(new.state_path.read_text())["active_path"] == str(new.active_path)
    assert record.jsonl_path.exists()


@pytest.mark.parametrize("operation", ["prune", "event", "stop"])
def test_stale_mapping_cleanup_cannot_remove_new_start(
    tmp_path, monkeypatch, operation
):
    old = start(tmp_path)
    old.active_path.unlink()
    stale, release, starting = Event(), Event(), Event()
    if operation == "prune":
        original = capture._stale_state_reason

        def paused(*args, **kwargs):
            reason = original(*args, **kwargs)
            stale.set()
            assert release.wait(10)
            return reason

        monkeypatch.setattr(capture, "_stale_state_reason", paused)

        def cleanup():
            return capture.prune_auto_capture_state(root=tmp_path)
    else:
        original = capture._read_state

        def paused(*args, **kwargs):
            state = original(*args, **kwargs)
            stale.set()
            assert release.wait(10)
            return state

        monkeypatch.setattr(capture, "_read_state", paused)

        def cleanup():
            with pytest.raises(FileNotFoundError):
                if operation == "event":
                    capture.append_auto_event(
                        root=tmp_path,
                        agent="codex",
                        platform_session_id="shared",
                        event_type="late",
                    )
                else:
                    capture.stop_auto_capture(
                        root=tmp_path, agent="codex", platform_session_id="shared"
                    )

    with ThreadPoolExecutor(2) as pool:
        removing = pool.submit(cleanup)
        try:
            assert stale.wait(10)
            observe_lock(monkeypatch, capture, starting)
            replacement = pool.submit(start, tmp_path)
            assert starting.wait(10)
            assert not replacement.done()
        finally:
            release.set()
        removing.result(timeout=10)
        new = replacement.result(timeout=10)
    assert new.active_path.exists()
    assert json.loads(new.state_path.read_text())["active_path"] == str(new.active_path)


def test_explicit_old_active_stop_preserves_current_identity_mapping(tmp_path):
    old = initialize(tmp_path)
    new = start(tmp_path)
    capture.stop_auto_capture(
        root=tmp_path, active=old, agent="codex", platform_session_id="shared"
    )
    assert new.active_path.exists()
    assert json.loads(new.state_path.read_text())["active_path"] == str(new.active_path)


def test_summary_staging_failure_releases_owner_and_removes_only_owned_partial(
    tmp_path, monkeypatch
):
    active = initialize(tmp_path)
    original = recorder.shutil.copyfileobj
    attempted = []

    def failing_copy(source, target):
        attempted.append(target.name)
        target.write(b"partial")
        raise OSError("copy interrupted")

    source = tmp_path / "summary.md"
    source.write_text("summary")
    monkeypatch.setattr(recorder.shutil, "copyfileobj", failing_copy)
    with pytest.raises(OSError, match="copy interrupted"):
        recorder.finalize_session(active, root=tmp_path, summary_file=source)
    assert attempted
    assert active.exists()
    assert not list((tmp_path / "pipy").glob("*/*/*.partial"))
    monkeypatch.setattr(recorder.shutil, "copyfileobj", original)
    recorder.append_event(active, root=tmp_path, event_type="recovered")
    record = recorder.finalize_session(active, root=tmp_path, summary_file=source)
    assert record.markdown_path.read_text() == "summary"


def test_existing_summary_partial_is_not_removed_on_exclusive_create_failure(tmp_path):
    active = initialize(tmp_path)
    directory = tmp_path / "pipy" / "2026" / "10"
    directory.mkdir(parents=True)
    foreign = directory / (active.stem + ".md.partial")
    foreign.write_text("other writer")
    with pytest.raises(FileExistsError):
        recorder.finalize_session(active, root=tmp_path, summary_text="summary")
    assert foreign.read_text() == "other writer"
    assert active.exists()
    recorder.append_event(active, root=tmp_path, event_type="still-active")


def test_event_holds_identity_owner_until_append_completes(tmp_path, monkeypatch):
    state = start(tmp_path)
    writing, release, stopping = Event(), Event(), Event()
    original = capture.append_event

    def paused_append(*args, **kwargs):
        if kwargs.get("event_type") == "concurrent.event":
            writing.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(capture, "append_event", paused_append)
    with ThreadPoolExecutor(2) as pool:
        event = pool.submit(
            capture.append_auto_event,
            root=tmp_path,
            agent="codex",
            platform_session_id="shared",
            event_type="concurrent.event",
        )
        try:
            assert writing.wait(10)
            observe_lock(monkeypatch, capture, stopping)
            stop = pool.submit(
                capture.stop_auto_capture,
                root=tmp_path,
                agent="codex",
                platform_session_id="shared",
            )
            assert stopping.wait(10)
            assert not stop.done()
        finally:
            release.set()
        event.result(timeout=10)
        record = stop.result(timeout=10)
    kinds = [
        json.loads(line)["type"] for line in record.jsonl_path.read_text().splitlines()
    ]
    assert kinds[-2:] == ["concurrent.event", "auto_capture.ended"]
    assert not state.state_path.exists()


def test_failed_initial_write_removes_owned_record_and_releases_owner(
    tmp_path, monkeypatch
):
    original = recorder._write_jsonl_event
    attempted = []

    def failed_write(handle, event):
        attempted.append(handle.name)
        raise OSError("initial write interrupted")

    monkeypatch.setattr(recorder, "_write_jsonl_event", failed_write)
    with pytest.raises(OSError, match="initial write interrupted"):
        initialize(tmp_path)
    assert attempted
    assert not list((tmp_path / ".in-progress" / "pipy").glob("*.jsonl"))
    monkeypatch.setattr(recorder, "_write_jsonl_event", original)
    assert initialize(tmp_path).exists()


def test_generated_identity_resumes_existing_explicit_identity_without_orphans(
    tmp_path,
):
    generated_stem = "2026-10-18T000000Z-test-codex-generation"
    existing = capture.start_auto_capture(
        root=tmp_path,
        agent="codex",
        slug="prior-explicit",
        platform_session_id=generated_stem,
        machine="test",
        now=NOW,
    )
    result = capture.start_auto_capture(
        root=tmp_path,
        agent="codex",
        slug="generation",
        machine="test",
        now=NOW,
    )
    assert result == existing
    assert json.loads(existing.state_path.read_text())["active_path"] == str(
        existing.active_path
    )
    assert list((tmp_path / ".in-progress" / "pipy").glob("*.jsonl")) == [
        existing.active_path
    ]
    events = [
        json.loads(line) for line in existing.active_path.read_text().splitlines()
    ]
    assert events[-1]["type"] == "auto_capture.resumed"
    assert events[-1]["payload"]["platform_session_id"] == generated_stem
    capture.append_auto_event(
        root=tmp_path,
        agent="codex",
        platform_session_id=generated_stem,
        event_type="after-resume",
    )
    record = capture.stop_auto_capture(
        root=tmp_path, agent="codex", platform_session_id=generated_stem
    )
    assert record.jsonl_path.exists()
    assert not existing.state_path.exists()


def test_failed_generated_resume_never_discards_reused_unpublished_path_twice(
    tmp_path, monkeypatch
):
    generated_stem = "2026-10-18T000000Z-test-codex-generation"
    existing = capture.start_auto_capture(
        root=tmp_path,
        agent="codex",
        slug="prior-explicit",
        platform_session_id=generated_stem,
        machine="test",
        now=NOW,
    )
    original_discard = capture.discard_active_session
    original_append = capture.append_event
    replacements = []
    discarded = []

    def discard_then_replace(active, **kwargs):
        discarded.append(active)
        original_discard(active, **kwargs)
        if len(discarded) == 1:
            replacement = initialize(tmp_path)
            assert replacement == active
            replacements.append(replacement)

    def fail_existing_resume(active, **kwargs):
        if kwargs.get("event_type") == "auto_capture.resumed":
            assert replacements and replacements[0].exists()
            raise OSError("resume interrupted")
        return original_append(active, **kwargs)

    monkeypatch.setattr(capture, "discard_active_session", discard_then_replace)
    monkeypatch.setattr(capture, "append_event", fail_existing_resume)
    with pytest.raises(OSError, match="resume interrupted"):
        capture.start_auto_capture(
            root=tmp_path, agent="codex", slug="generation", machine="test", now=NOW
        )
    assert discarded == replacements
    assert replacements[0].exists()
    assert existing.active_path.exists()
    assert json.loads(existing.state_path.read_text())["active_path"] == str(
        existing.active_path
    )
    # Both owners released on failure; independent and existing records remain writable.
    recorder.append_event(
        replacements[0], root=tmp_path, event_type="replacement-survived"
    )
    monkeypatch.setattr(capture, "append_event", original_append)
    capture.append_auto_event(
        root=tmp_path,
        agent="codex",
        platform_session_id=generated_stem,
        event_type="existing-survived",
    )
