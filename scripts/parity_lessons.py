#!/usr/bin/env python3
"""Deterministic operations on the parity-loop lesson ledger.

CLI + importable library. The ledger is `docs/parity-loop/lessons/lessons.jsonl`:
one JSON object per line, exactly one line per lesson `id`. Judgment (what is a
lesson, what edit fixes it) stays with agents; this module enforces structure and
materialization. See docs/superpowers/specs/2026-06-22-parity-loop-learning-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

STATUSES = ("open", "applied", "rejected")
TRIGGERS = ("recurring-review-finding", "gate-failure", "wrong-turn", "better-approach")
TARGET_AREAS = ("skill-body", "wrapper", "docs", "harness", "tests")
AGENTS = ("claude", "codex", "pi", "pipy")
INSTRUCTION_AREAS = ("skill-body", "wrapper")
REQUIRED_FIELDS = ("id", "date", "skill", "gap", "agent", "trigger", "lesson", "target_area", "status")
ID_RE = r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{6}\Z"
SHA_RE = r"^[0-9a-f]{40}\Z"
INSTRUCTION_BODIES = ("docs/parity-loop/skill-body.md", "docs/parity-loop/improve-body.md")
LEDGER_PREFIXES = ("docs/parity-loop/lessons/", "docs/parity-loop/runs/")


def load_lessons(path):
    """Return the list of lesson records. Missing/empty file -> []."""
    p = Path(path)
    if not p.exists():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _write_lessons(path, records):
    """Atomically rewrite the ledger as one compact JSON object per line."""
    p = Path(path)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)


def _norm(text):
    """Lowercase + collapse whitespace, for duplicate detection."""
    return " ".join(text.lower().split())


CALLER_REQUIRED = ("skill", "gap", "agent", "trigger", "lesson", "target_area")


def append_lesson(path, record, *, today, rand):
    """Append a new lesson; assign id = f"{today}-{rand}", date, and status 'open'.

    `record` must NOT contain id/status, must include every field in
    CALLER_REQUIRED (non-empty) with valid enum values. Raises ValueError on a
    precondition violation, missing field, bad enum, generated-id collision, or a
    near-duplicate (same skill + target_area + normalized lesson). This guarantees
    a freshly appended ledger still passes validate().
    """
    if "id" in record or "status" in record:
        raise ValueError("record must not contain 'id' or 'status'")
    for field in CALLER_REQUIRED:
        if not record.get(field):
            raise ValueError(f"record missing required field '{field}'")
    if record["agent"] not in AGENTS:
        raise ValueError(f"agent not in {AGENTS}")
    if record["trigger"] not in TRIGGERS:
        raise ValueError(f"trigger not in {TRIGGERS}")
    if record["target_area"] not in TARGET_AREAS:
        raise ValueError(f"target_area not in {TARGET_AREAS}")
    existing = load_lessons(path)
    new_id = f"{today}-{rand}"
    if not re.match(ID_RE, new_id):
        raise ValueError(f"generated id {new_id!r} does not match {ID_RE}")
    if any(r.get("id") == new_id for r in existing):
        raise ValueError(f"generated id collision: {new_id}")
    key = (record["skill"], record["target_area"], _norm(record["lesson"]))
    for r in existing:
        if (r.get("skill"), r.get("target_area"), _norm(r.get("lesson", ""))) == key:
            raise ValueError(f"duplicate lesson for {key[0]}/{key[1]}")
    new = dict(record)
    new["id"] = new_id
    new["date"] = today
    new["status"] = "open"
    existing.append(new)
    _write_lessons(path, existing)
    return new["id"]


def list_lessons(path, status=None):
    """Return records, optionally filtered to a single status."""
    rows = load_lessons(path)
    if status is None:
        return rows
    return [r for r in rows if r.get("status") == status]


def _schema_errors(records):
    """Structural errors: parse handled by caller; here check fields/enums/ids."""
    errors = []
    seen_ids = set()
    for i, rec in enumerate(records):
        rid = str(rec.get("id", f"<line {i + 1}>"))
        for field in REQUIRED_FIELDS:
            if field not in rec or rec[field] in (None, ""):
                errors.append(f"{rid}: missing required field '{field}'")
        if "id" in rec and not re.match(ID_RE, str(rec["id"])):
            errors.append(f"{rid}: id must match {ID_RE}")
        if rec.get("status") not in STATUSES:
            errors.append(f"{rid}: status not in {STATUSES}")
        if rec.get("trigger") not in TRIGGERS:
            errors.append(f"{rid}: trigger not in {TRIGGERS}")
        if rec.get("target_area") not in TARGET_AREAS:
            errors.append(f"{rid}: target_area not in {TARGET_AREAS}")
        if rec.get("agent") not in AGENTS:
            errors.append(f"{rid}: agent not in {AGENTS}")
        if "id" in rec:
            key = str(rec["id"])  # str() guards unhashable JSON ids (e.g. [])
            if key in seen_ids:
                errors.append(f"{key}: duplicate id")
            seen_ids.add(key)
    return errors


def validate(path, repo_root):
    """Return a list of human-readable error strings; empty means valid."""
    p = Path(path)
    errors = []
    records = []
    if p.exists():
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"<line {n}>: JSON parse error: {exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"<line {n}>: each line must be a JSON object")
                continue
            records.append(obj)
    errors.extend(_schema_errors(records))
    errors.extend(_materialization_errors(records, Path(repo_root)))
    return errors


def _git(repo_root, *args):
    """Run git; return CompletedProcess (never raises on non-zero)."""
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )


def _is_ledger_or_scratch(rel):
    return any(rel.startswith(prefix) for prefix in LEDGER_PREFIXES)


def _matches_target_area(target_area, rel):
    if _is_ledger_or_scratch(rel):
        return False
    if target_area == "skill-body":
        return rel in INSTRUCTION_BODIES
    if target_area == "wrapper":
        return (
            rel == "AGENTS.md"
            or rel.startswith(".claude/skills/")
            or rel.startswith(".pipy/skills/")
            or rel.startswith(".pi/skills/")
        )
    if target_area == "docs":
        return rel.startswith("docs/") and rel not in INSTRUCTION_BODIES
    if target_area == "tests":
        return rel.startswith("tests/")
    if target_area == "harness":
        return rel.startswith("src/") or rel.startswith("scripts/")
    return False


def _record_materialization_errors(rec, repo_root):
    """Materialization errors for ONE record (empty if valid or not applied/rejected).

    Shared by `_materialization_errors` (whole-ledger validate) and the CLI
    `mark applied` dry-run, so marking and validating enforce identical rules.
    """
    rid = str(rec.get("id", "<unknown>"))  # str() guards non-string ids
    status = rec.get("status")
    if status not in ("applied", "rejected"):
        return []
    resolution = rec.get("resolution")
    if not isinstance(resolution, dict):
        return [f"{rid}: resolution must be an object"]
    if status == "rejected":
        errors = []
        if not resolution.get("reason"):
            errors.append(f"{rid}: rejected requires non-empty resolution.reason")
        if not resolution.get("signed_off_by"):
            errors.append(f"{rid}: rejected requires resolution.signed_off_by")
        return errors
    # status == "applied"
    sha = resolution.get("sha")
    if not isinstance(sha, str) or not sha:
        return [f"{rid}: applied requires a string resolution.sha"]
    if not re.match(SHA_RE, sha):
        return [f"{rid}: resolution.sha must be a full 40-char hex commit id, not a ref"]
    if _git(repo_root, "rev-parse", "--verify", f"{sha}^{{commit}}").returncode != 0:
        return [f"{rid}: resolution.sha {sha} does not resolve to a commit"]
    if _git(repo_root, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        return [f"{rid}: resolution.sha {sha} is not an ancestor of HEAD"]
    errors = []
    message = _git(repo_root, "log", "-1", "--format=%B", sha).stdout
    if rid not in message:
        errors.append(f"{rid}: commit message of {sha} does not name the lesson id")
    changed = _git(
        repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha
    ).stdout.split()
    qualifying = [f for f in changed if _matches_target_area(rec.get("target_area"), f)]
    if not qualifying:
        errors.append(
            f"{rid}: commit {sha} materializes no non-ledger file for "
            f"target_area '{rec.get('target_area')}'"
        )
    if rec.get("target_area") in INSTRUCTION_AREAS and not resolution.get("signed_off_by"):
        errors.append(f"{rid}: instruction-area applied requires resolution.signed_off_by")
    return errors


def _materialization_errors(records, repo_root):
    errors = []
    for rec in records:
        errors.extend(_record_materialization_errors(rec, Path(repo_root)))
    return errors


def _find(records, lesson_id):
    for r in records:
        if r.get("id") == lesson_id:
            return r
    raise KeyError(lesson_id)


def mark_applied(path, lesson_id, sha, repo_root, signed_off_by=None):
    """Transition a lesson to 'applied', enforcing materialization.

    Builds the prospective record and runs the SAME check `validate` uses
    (`_record_materialization_errors`) against `repo_root`; raises ValueError if
    the result would be invalid, so the library never writes a bad applied record.
    """
    records = load_lessons(path)
    rec = _find(records, lesson_id)  # raises KeyError for unknown id
    resolution = {"sha": sha}
    if signed_off_by:
        resolution["signed_off_by"] = signed_off_by
    candidate = dict(rec)
    candidate["status"] = "applied"
    candidate["resolution"] = resolution
    errs = _record_materialization_errors(candidate, Path(repo_root))
    if errs:
        raise ValueError("; ".join(errs))
    rec["status"] = "applied"
    rec["resolution"] = resolution
    _write_lessons(path, records)


def mark_rejected(path, lesson_id, reason, signed_off_by):
    """Transition a lesson to 'rejected'. Requires reason AND sign-off (no git)."""
    if not reason:
        raise ValueError(f"{lesson_id}: rejection requires a non-empty reason")
    if not signed_off_by:
        raise ValueError(f"{lesson_id}: rejection requires signed_off_by")
    records = load_lessons(path)
    rec = _find(records, lesson_id)
    rec["status"] = "rejected"
    rec["resolution"] = {"reason": reason, "signed_off_by": signed_off_by}
    _write_lessons(path, records)


DEFAULT_LEDGER = "docs/parity-loop/lessons/lessons.jsonl"


def main(argv=None):
    parser = argparse.ArgumentParser(description="parity-loop lesson ledger helper")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate")

    p_append = sub.add_parser("append")
    p_append.add_argument("--json", dest="record_json", required=True,
                          help="record without id/status")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=STATUSES, default=None)
    p_list.add_argument("--json", action="store_true")

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("id")
    p_mark.add_argument("new_status", choices=("applied", "rejected"))
    p_mark.add_argument("--sha")
    p_mark.add_argument("--reason")
    p_mark.add_argument("--signed-off-by", dest="signed_off_by")

    args = parser.parse_args(argv)
    ledger = args.ledger

    if args.command == "validate":
        errors = validate(ledger, repo_root=args.repo)
        for e in errors:
            print(e)
        return 1 if errors else 0

    if args.command == "append":
        record = json.loads(args.record_json)
        new_id = append_lesson(
            ledger, record, today=date.today().isoformat(), rand=os.urandom(3).hex()
        )
        print(new_id)
        return 0

    if args.command == "list":
        rows = list_lessons(ledger, status=args.status)
        if args.json:
            print(json.dumps(rows))
        else:
            for r in rows:
                print(f"{r.get('id')}  {r.get('status')}  {r.get('target_area')}  {r.get('lesson')}")
        return 0

    if args.command == "mark":
        if args.new_status == "applied":
            if not args.sha:
                print("mark applied requires --sha")
                return 1
            # Dry-run the materialization check against --repo BEFORE mutating the
            # ledger, so the CLI never writes a knowingly-invalid applied record.
            records = load_lessons(ledger)
            try:
                rec = _find(records, args.id)
            except KeyError:
                print(f"unknown lesson id: {args.id}")
                return 1
            candidate = dict(rec)
            candidate["status"] = "applied"
            resolution = {"sha": args.sha}
            if args.signed_off_by:
                resolution["signed_off_by"] = args.signed_off_by
            candidate["resolution"] = resolution
            errs = _record_materialization_errors(candidate, Path(args.repo))
            if errs:
                for e in errs:
                    print(e)
                return 1
            mark_applied(ledger, args.id, sha=args.sha, repo_root=args.repo,
                         signed_off_by=args.signed_off_by)
        else:
            if not args.reason or not args.signed_off_by:
                print("mark rejected requires --reason and --signed-off-by")
                return 1
            try:
                mark_rejected(ledger, args.id, reason=args.reason,
                              signed_off_by=args.signed_off_by)
            except KeyError:
                print(f"unknown lesson id: {args.id}")
                return 1
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
