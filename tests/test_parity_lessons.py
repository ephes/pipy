"""Unit tests for the parity-loop lesson ledger helper."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

# All imports live at the top from the start: later tasks add subprocess/json-using
# helpers, and ruff (run in `just check`, Task 8) rejects mid-file imports (E402).
# There is no per-commit ruff gate (no .pre-commit-config.yaml), so the unused
# imports in the early-task commits are harmless and become used by Task 5+.

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parity_lessons.py"
_spec = importlib.util.spec_from_file_location("parity_lessons", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
parity_lessons: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity_lessons)


def test_load_missing_and_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert parity_lessons.load_lessons(missing) == []
    empty = tmp_path / "lessons.jsonl"
    empty.write_text("", encoding="utf-8")
    assert parity_lessons.load_lessons(empty) == []


def _base_record(**over: Any) -> dict[str, Any]:
    rec = {
        "skill": "pipy-parity-loop",
        "gap": "demo-gap",
        "agent": "claude",
        "trigger": "gate-failure",
        "lesson": "Run just check before every review.",
        "target_area": "skill-body",
    }
    rec.update(over)
    return rec


def test_append_assigns_id_and_open_status(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    new_id = parity_lessons.append_lesson(
        ledger, _base_record(), today="2026-06-22", rand="a3f9c1"
    )
    assert new_id == "2026-06-22-a3f9c1"
    rows = parity_lessons.load_lessons(ledger)
    assert len(rows) == 1
    assert rows[0]["id"] == "2026-06-22-a3f9c1"
    assert rows[0]["status"] == "open"
    assert rows[0]["date"] == "2026-06-22"


def test_append_refuses_near_duplicate(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    # Same skill+target_area+normalized lesson (case/whitespace folded) -> duplicate.
    dup = _base_record(lesson="  RUN   just check  before every review.  ")
    try:
        parity_lessons.append_lesson(ledger, dup, today="2026-06-22", rand="bbbbbb")
        raise AssertionError("expected a duplicate to be refused")
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    assert len(parity_lessons.load_lessons(ledger)) == 1


def test_append_rejects_id_or_status_in_input(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    for bad in (_base_record(id="x"), _base_record(status="open")):
        try:
            parity_lessons.append_lesson(ledger, bad, today="2026-06-22", rand="aaaaaa")
            raise AssertionError("must reject id/status in input")
        except ValueError:
            pass
    assert parity_lessons.load_lessons(ledger) == []


def test_append_rejects_missing_field_and_bad_enum(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    no_gap = _base_record()
    del no_gap["gap"]
    for bad in (no_gap, _base_record(trigger="nope"), _base_record(agent="bogus")):
        try:
            parity_lessons.append_lesson(ledger, bad, today="2026-06-22", rand="aaaaaa")
            raise AssertionError("must reject invalid record")
        except ValueError:
            pass


def test_append_rejects_id_collision(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    try:
        parity_lessons.append_lesson(
            ledger, _base_record(lesson="different"), today="2026-06-22", rand="aaaaaa"
        )
        raise AssertionError("same generated id must collide")
    except ValueError as exc:
        assert "collision" in str(exc).lower()


def test_append_rejects_malformed_generated_id(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    try:
        parity_lessons.append_lesson(ledger, _base_record(), today="2026/06/22", rand="zz")
        raise AssertionError("malformed today/rand must be refused")
    except ValueError as exc:
        assert "match" in str(exc).lower()


def test_list_filters_by_status(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    parity_lessons.append_lesson(
        ledger, _base_record(lesson="Second lesson."), today="2026-06-22", rand="bbbbbb"
    )
    assert len(parity_lessons.list_lessons(ledger)) == 2
    assert len(parity_lessons.list_lessons(ledger, status="open")) == 2
    assert parity_lessons.list_lessons(ledger, status="applied") == []


def _write_raw(ledger: Path, records: list[dict[str, Any]]) -> None:
    ledger.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )


def _valid_open(**over: Any) -> dict[str, Any]:
    rec = {
        "id": "2026-06-22-a3f9c1",
        "date": "2026-06-22",
        "skill": "pipy-parity-loop",
        "gap": "demo",
        "agent": "claude",
        "trigger": "gate-failure",
        "lesson": "x",
        "target_area": "docs",
        "status": "open",
    }
    rec.update(over)
    return rec


def test_validate_passes_empty(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    ledger.write_text("", encoding="utf-8")
    assert parity_lessons.validate(ledger, repo_root=tmp_path) == []


def test_validate_open_record_ok(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open()])
    assert parity_lessons.validate(ledger, repo_root=tmp_path) == []


def test_validate_catches_bad_enum_and_missing_field(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(trigger="nonsense")])
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert any("trigger" in e for e in errs)
    missing = _valid_open()
    del missing["gap"]
    _write_raw(ledger, [missing])
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert any("gap" in e for e in errs)


def test_validate_catches_bad_id_and_duplicate(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(id="BADID")])
    assert any("id" in e for e in parity_lessons.validate(ledger, repo_root=tmp_path))
    _write_raw(ledger, [_valid_open(), _valid_open(lesson="y")])  # same id twice
    assert any("duplicate" in e.lower() for e in parity_lessons.validate(ledger, repo_root=tmp_path))


def test_validate_catches_malformed_json(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    ledger.write_text("{not json}\n", encoding="utf-8")
    assert any("parse" in e.lower() or "json" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=tmp_path))


def test_validate_catches_non_object_line(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    # Valid JSON, but not an object — must be reported, not crash.
    ledger.write_text('[]\n"x"\n', encoding="utf-8")
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert sum("must be a JSON object" in e for e in errs) == 2


def test_validate_handles_unhashable_id_without_crash(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    # An object whose id is an unhashable JSON value must not crash validate().
    ledger.write_text('{"id": [], "status": "open"}\n', encoding="utf-8")
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert errs  # reports bad id + missing fields, does not raise


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _commit_file(repo: Path, rel: str, message: str) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _applied(rid: str, sha: str, target_area: str, **over: Any) -> dict[str, Any]:
    rec = _valid_open(id=rid, target_area=target_area, status="applied")
    rec["resolution"] = {"sha": sha}
    rec.update(over)
    return rec


def test_validate_applied_ok(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/guide.md", f"docs fix\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert parity_lessons.validate(ledger, repo_root=repo) == []


def test_validate_applied_bad_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied("2026-06-22-aaaaaa", "0123456789abcdef0123456789abcdef01234567", "docs")])
    assert any("resolve" in e.lower() or "ancestor" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_rejects_ref_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    # A real commit exists, but the ledger stores a mutable ref instead of a SHA.
    _commit_file(repo, "docs/guide.md", f"docs\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, "HEAD", "docs")])
    errs = parity_lessons.validate(ledger, repo_root=repo)
    assert any("hex" in e.lower() for e in errs)


def test_validate_applied_nonstring_sha_no_crash(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _applied("2026-06-22-aaaaaa", "x", "docs")
    rec["resolution"]["sha"] = []  # not a string
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)  # must not raise
    assert any("string resolution.sha" in e for e in errs)


def test_validate_applied_not_ancestor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    # Commit on a side branch, then return to main so the sha is not a HEAD ancestor.
    _git(repo, "checkout", "-q", "-b", "side")
    sha = _commit_file(repo, "docs/guide.md", f"side\n\nCloses lessons: {rid}")
    _git(repo, "checkout", "-q", "-")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("ancestor" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_wrong_area(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "tests/test_x.py", f"t\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])  # claims docs, touched tests/
    assert any("target_area" in e or "materializ" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_ledger_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/parity-loop/lessons/lessons.jsonl", f"x\n\nCloses lessons: {rid}")
    ledger = repo / "other.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("materializ" in e.lower() or "ledger" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_message_missing_id(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/guide.md", "docs fix without id reference")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("message" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_instruction_needs_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/parity-loop/skill-body.md", f"edit\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "skill-body")])  # no signed_off_by
    assert any("sign" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))
    signed = _applied(rid, sha, "skill-body")
    signed["resolution"]["signed_off_by"] = "jochen"
    _write_raw(ledger, [signed])
    assert parity_lessons.validate(ledger, repo_root=repo) == []


def test_validate_resolution_not_object(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _valid_open(id="2026-06-22-aaaaaa", status="applied")
    rec["resolution"] = "bad"  # not a dict
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)  # must not crash
    assert any("resolution must be an object" in e for e in errs)


def test_validate_rejected_requires_reason_and_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _valid_open(id="2026-06-22-aaaaaa", status="rejected")
    rec["resolution"] = {"reason": "", "signed_off_by": ""}
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)
    assert any("reason" in e.lower() for e in errs)
    assert any("sign" in e.lower() for e in errs)


def test_mark_applied_materializing_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="docs"), today="2026-06-22", rand="cccccc"
    )
    sha = _commit_file(repo, "docs/guide.md", f"docs fix\n\nCloses lessons: {lid}")
    parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo)
    row = parity_lessons.list_lessons(ledger)[0]
    assert row["status"] == "applied"
    assert row["resolution"]["sha"] == sha


def test_mark_applied_rejects_nonmaterializing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="docs"), today="2026-06-22", rand="dddddd"
    )
    # 'HEAD' is a ref, not a 40-char hex sha -> refused; ledger stays open.
    try:
        parity_lessons.mark_applied(ledger, lid, sha="HEAD", repo_root=repo)
        raise AssertionError("non-materializing sha must be refused")
    except ValueError:
        pass
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"


def test_mark_applied_instruction_area_requires_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="skill-body"), today="2026-06-22", rand="eeeeee"
    )
    sha = _commit_file(
        repo, "docs/parity-loop/skill-body.md", f"edit\n\nCloses lessons: {lid}"
    )
    try:
        parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo)
        raise AssertionError("instruction-area apply must require sign-off")
    except ValueError as exc:
        assert "sign" in str(exc).lower()
    parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo, signed_off_by="jochen")
    assert parity_lessons.list_lessons(ledger)[0]["resolution"]["signed_off_by"] == "jochen"


def test_mark_unknown_id_raises(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="ffffff")
    try:
        parity_lessons.mark_applied(ledger, "2026-06-22-000000", sha="0" * 40, repo_root=repo)
        raise AssertionError("unknown id must raise")
    except KeyError:
        pass


def test_mark_rejected_requires_reason_and_signoff(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    lid = parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="ababab")
    try:
        parity_lessons.mark_rejected(ledger, lid, reason="", signed_off_by="judge")
        raise AssertionError("empty reason must be refused")
    except ValueError:
        pass
    parity_lessons.mark_rejected(ledger, lid, reason="Not reusable.", signed_off_by="judge")
    row = parity_lessons.list_lessons(ledger)[0]
    assert row["status"] == "rejected"
    assert row["resolution"] == {"reason": "Not reusable.", "signed_off_by": "judge"}


def _run_cli(repo: Path, ledger: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(_MOD_PATH), "--ledger", str(ledger), "--repo", str(repo), *args],
        capture_output=True, text=True,
    )


def test_cli_validate_empty_exit_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    ledger.write_text("", encoding="utf-8")
    result = _run_cli(repo, ledger, "validate")
    assert result.returncode == 0, result.stderr


def test_cli_validate_reports_errors_exit_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    ledger.write_text('{"id":"BADID","status":"open"}\n', encoding="utf-8")
    result = _run_cli(repo, ledger, "validate")
    assert result.returncode == 1
    assert "id" in (result.stdout + result.stderr)


def test_cli_list_open_json(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open()])
    result = _run_cli(repo, ledger, "list", "--status", "open", "--json")
    assert result.returncode == 0
    assert "2026-06-22-a3f9c1" in result.stdout


def test_cli_mark_applied_without_sha_exits_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(target_area="docs")])
    result = _run_cli(repo, ledger, "mark", "2026-06-22-a3f9c1", "applied")
    assert result.returncode == 1
    assert "sha" in (result.stdout + result.stderr).lower()
    # The ledger must be untouched (still open), not written with a null sha.
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"


def test_cli_mark_applied_nonmaterializing_sha_exits_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(target_area="docs")])
    # 'HEAD' is a ref, not a 40-char hex sha — the dry-run materialization check
    # must reject it and leave the ledger untouched.
    result = _run_cli(repo, ledger, "mark", "2026-06-22-a3f9c1", "applied", "--sha", "HEAD")
    assert result.returncode == 1
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"


def test_real_repo_ledger_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ledger = repo_root / "docs" / "parity-loop" / "lessons" / "lessons.jsonl"
    assert ledger.exists(), "the tracked ledger must exist"
    errors = parity_lessons.validate(ledger, repo_root=repo_root)
    assert errors == [], f"checked-in ledger is invalid: {errors}"
