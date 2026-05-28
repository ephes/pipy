"""Compare pipy and Pi terminal-screen verification artifacts."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_FINDING_KEYS = ("prompt", "expected_output", "working", "status", "cwd")


@dataclass(frozen=True, slots=True)
class MetricPoint:
    row: int
    column: int
    attr: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DeltaRecord:
    frame_index: int
    phase: str
    metric: str
    reference_label: str
    reference_row: int | None
    reference_column: int | None
    target_label: str
    target_row: int | None
    target_column: int | None
    row_delta: int | None
    column_delta: int | None
    within_tolerance: bool | None
    attributes_match: bool | None
    reference_attr: dict[str, Any] | None
    target_attr: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ViewportDeltaRecord:
    frame_index: int
    phase: str
    row: int
    reference_label: str
    reference_text: str
    target_label: str
    target_text: str


@dataclass(frozen=True, slots=True)
class BackgroundDeltaRecord:
    frame_index: int
    phase: str
    metric: str
    reference_label: str
    reference_rows: list[dict[str, Any]]
    target_label: str
    target_rows: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _ComparisonPair:
    phase: str
    reference: dict[str, Any] | None
    target: dict[str, Any] | None


def compare_screen_metrics(
    *,
    reference_jsonl: Path,
    target_jsonl: Path,
    out_json: Path,
    out_tsv: Path,
    anomalies_tsv: Path,
    reference_label: str = "pipy",
    target_label: str = "pi",
    max_row_delta: int = 0,
    max_column_delta: int = 0,
    viewport_json: Path | None = None,
    viewport_tsv: Path | None = None,
) -> dict[str, Any]:
    """Compare two screen-metrics JSONL files and write delta artifacts."""

    reference_records = _load_records(reference_jsonl)
    target_records = _load_records(target_jsonl)
    pairs = _comparison_pairs(reference_records, target_records)
    deltas: list[DeltaRecord] = []
    viewport_deltas: list[ViewportDeltaRecord] = []
    background_deltas: list[BackgroundDeltaRecord] = []
    anomalies: list[tuple[str, str, str]] = []

    for pair_index, pair in enumerate(pairs):
        reference = pair.reference or {}
        target = pair.target or {}
        phase = pair.phase
        for metric in _FINDING_KEYS:
            reference_point = _finding_point(reference, metric)
            target_point = _finding_point(target, metric)
            if reference_point is None and target_point is None:
                continue
            deltas.append(
                _delta_record(
                    frame_index=pair_index,
                    phase=phase,
                    metric=metric,
                    reference_label=reference_label,
                    reference_point=reference_point,
                    target_label=target_label,
                    target_point=target_point,
                    max_row_delta=max_row_delta,
                    max_column_delta=max_column_delta,
                )
            )
        for metric, reference_point, target_point in (
            (
                "input_row",
                _row_point(reference.get("inferred_input_row")),
                _row_point(target.get("inferred_input_row")),
            ),
            (
                "live_cursor",
                _cursor_point(reference.get("live_cursor")),
                _cursor_point(target.get("live_cursor")),
            ),
            (
                "drawn_cursor",
                _reverse_cursor_point(reference),
                _reverse_cursor_point(target),
            ),
        ):
            deltas.append(
                _delta_record(
                    frame_index=pair_index,
                    phase=phase,
                    metric=metric,
                    reference_label=reference_label,
                    reference_point=reference_point,
                    target_label=target_label,
                    target_point=target_point,
                    max_row_delta=max_row_delta,
                    max_column_delta=max_column_delta,
                )
            )
        viewport_deltas.extend(
            _viewport_delta_records(
                frame_index=pair_index,
                phase=phase,
                reference_label=reference_label,
                reference=pair.reference,
                target_label=target_label,
                target=pair.target,
            )
        )
        background_delta = _background_delta_record(
            frame_index=pair_index,
            phase=phase,
            reference_label=reference_label,
            reference=pair.reference,
            target_label=target_label,
            target=pair.target,
        )
        if background_delta is not None:
            background_deltas.append(background_delta)

    for delta in deltas:
        if delta.within_tolerance is False and delta.phase == "final":
            anomalies.append(
                (
                    f"{delta.frame_index}:{delta.phase}",
                    "error",
                    (
                        f"{delta.metric} delta row={delta.row_delta} "
                        f"column={delta.column_delta} exceeds tolerance"
                    ),
                )
            )
        if delta.attributes_match is False:
            anomalies.append(
                (
                    f"{delta.frame_index}:{delta.phase}",
                    "error",
                    f"{delta.metric} cell attributes differ",
                )
            )
        if delta.within_tolerance is None and _required_metric(delta.metric, delta.phase):
            anomalies.append(
                (
                    f"{delta.frame_index}:{delta.phase}",
                    "error",
                    f"{delta.metric} missing on one side of comparison",
                )
            )
    if viewport_deltas:
        final_viewport_delta_count = sum(
            1 for delta in viewport_deltas if delta.phase == "final"
        )
        if final_viewport_delta_count:
            anomalies.append(
                (
                    "final",
                    "error",
                    f"final viewport differs on {final_viewport_delta_count} rows",
                )
            )
            for viewport_delta in viewport_deltas[:20]:
                if viewport_delta.phase != "final":
                    continue
                anomalies.append(
                    (
                        f"{viewport_delta.frame_index}:{viewport_delta.phase}",
                        "error",
                        (
                            f"viewport row {viewport_delta.row} differs: "
                            f"{viewport_delta.reference_label}="
                            f"{viewport_delta.reference_text!r} "
                            f"{viewport_delta.target_label}="
                            f"{viewport_delta.target_text!r}"
                        ),
                    )
                )

    for background_delta in background_deltas:
        if background_delta.phase != "final":
            continue
        anomalies.append(
            (
                f"{background_delta.frame_index}:{background_delta.phase}",
                "error",
                (
                    f"{background_delta.metric} differ: "
                    f"{background_delta.reference_label}="
                    f"{background_delta.reference_rows!r} "
                    f"{background_delta.target_label}="
                    f"{background_delta.target_rows!r}"
                ),
            )
        )

    report = {
        "reference_label": reference_label,
        "target_label": target_label,
        "reference_metrics": str(reference_jsonl),
        "target_metrics": str(target_jsonl),
        "compared_frames": len(pairs),
        "delta_count": len(deltas),
        "anomaly_count": len(anomalies),
        "viewport_delta_count": len(viewport_deltas),
        "final_viewport_delta_count": sum(
            1 for delta in viewport_deltas if delta.phase == "final"
        ),
        "prompt_background_delta_count": len(background_deltas),
        "final_prompt_background_delta_count": sum(
            1 for delta in background_deltas if delta.phase == "final"
        ),
        "max_row_delta": max_row_delta,
        "max_column_delta": max_column_delta,
    }
    _write_json(out_json, [asdict(delta) for delta in deltas])
    _write_tsv(out_tsv, deltas)
    if viewport_json is not None:
        _write_json(viewport_json, [asdict(delta) for delta in viewport_deltas])
    if viewport_tsv is not None:
        _write_viewport_tsv(viewport_tsv, viewport_deltas)
    _write_anomalies(anomalies_tsv, anomalies)
    return report


def _comparison_pairs(
    reference_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
) -> list[_ComparisonPair]:
    """Pair active frames by sample order and final frames by phase.

    The two CLIs often settle after different numbers of active samples. Pairing
    only by JSONL order can accidentally compare pipy's final frame with Pi's
    still-working active frame and then skip the actual Pi final frame.
    """

    reference_selected = _comparable_records(reference_records)
    target_selected = _comparable_records(target_records)
    reference_active = _phase_records(reference_selected, "active")
    target_active = _phase_records(target_selected, "active")
    pairs = [
        _ComparisonPair("active", reference, target)
        for reference, target in zip(reference_active, target_active)
    ]
    reference_final = _last_phase_record(reference_selected, "final")
    target_final = _last_phase_record(target_selected, "final")
    if reference_final is not None or target_final is not None:
        pairs.append(_ComparisonPair("final", reference_final, target_final))
    if pairs:
        return pairs
    return [
        _ComparisonPair(
            str(target.get("phase") or reference.get("phase") or ""),
            reference,
            target,
        )
        for reference, target in zip(reference_selected, target_selected)
    ]


def _phase_records(
    records: list[dict[str, Any]], phase: str
) -> list[dict[str, Any]]:
    return [record for record in records if record.get("phase") == phase]


def _last_phase_record(
    records: list[dict[str, Any]], phase: str
) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("phase") == phase:
            return record
    return None


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _comparable_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [record for record in records if record.get("phase") != "startup"]
    return selected if selected else records


def _finding_point(record: dict[str, Any], key: str) -> MetricPoint | None:
    findings = record.get("findings")
    if not isinstance(findings, dict):
        return None
    values = findings.get(key)
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        return None
    return _point(first.get("row"), first.get("column"), first.get("attr"))


def _cursor_point(value: Any) -> MetricPoint | None:
    if not isinstance(value, dict):
        return None
    return _point(value.get("cursor_y"), value.get("cursor_x"), None)


def _reverse_cursor_point(record: dict[str, Any]) -> MetricPoint | None:
    cells = record.get("reverse_cells")
    if not isinstance(cells, list) or not cells:
        return None
    first = cells[0]
    if not isinstance(first, dict):
        return None
    return _point(first.get("row"), first.get("column"), first.get("attr"))


def _row_point(value: Any) -> MetricPoint | None:
    if not isinstance(value, int):
        return None
    return MetricPoint(value, 0)


def _point(row: Any, column: Any, attr: Any) -> MetricPoint | None:
    if isinstance(row, int) and isinstance(column, int):
        attr_value = attr if isinstance(attr, dict) else None
        return MetricPoint(row, column, attr_value)
    return None


def _delta_record(
    *,
    frame_index: int,
    phase: str,
    metric: str,
    reference_label: str,
    reference_point: MetricPoint | None,
    target_label: str,
    target_point: MetricPoint | None,
    max_row_delta: int,
    max_column_delta: int,
) -> DeltaRecord:
    reference_row = reference_point.row if reference_point else None
    reference_column = reference_point.column if reference_point else None
    target_row = target_point.row if target_point else None
    target_column = target_point.column if target_point else None
    reference_attr = reference_point.attr if reference_point else None
    target_attr = target_point.attr if target_point else None
    row_delta = (
        target_row - reference_row
        if target_row is not None and reference_row is not None
        else None
    )
    column_delta = (
        target_column - reference_column
        if target_column is not None and reference_column is not None
        else None
    )
    if row_delta is None or column_delta is None:
        within_tolerance = None
    else:
        within_tolerance = (
            abs(row_delta) <= max_row_delta
            and abs(column_delta) <= max_column_delta
        )
    if reference_point is None or target_point is None:
        attributes_match = None
    elif reference_attr is None and target_attr is None:
        attributes_match = None
    elif reference_attr is not None and target_attr is not None:
        attributes_match = reference_attr == target_attr
    else:
        attributes_match = False
    return DeltaRecord(
        frame_index=frame_index,
        phase=phase,
        metric=metric,
        reference_label=reference_label,
        reference_row=reference_row,
        reference_column=reference_column,
        target_label=target_label,
        target_row=target_row,
        target_column=target_column,
        row_delta=row_delta,
        column_delta=column_delta,
        within_tolerance=within_tolerance,
        attributes_match=attributes_match,
        reference_attr=reference_attr,
        target_attr=target_attr,
    )


def _required_metric(metric: str, phase: str) -> bool:
    if phase == "final":
        return metric in {
            "prompt",
            "expected_output",
            "status",
            "cwd",
            "input_row",
            "live_cursor",
            "drawn_cursor",
        }
    return metric in {"prompt", "status", "cwd", "input_row"}


def _viewport_delta_records(
    *,
    frame_index: int,
    phase: str,
    reference_label: str,
    reference: dict[str, Any] | None,
    target_label: str,
    target: dict[str, Any] | None,
) -> list[ViewportDeltaRecord]:
    if phase != "final":
        return []
    reference_rows = _viewport_rows(reference)
    target_rows = _viewport_rows(target)
    if reference_rows is None and target_rows is None:
        return []
    reference_rows = reference_rows or []
    target_rows = target_rows or []
    rows: list[ViewportDeltaRecord] = []
    max_rows = max(len(reference_rows), len(target_rows))
    for row in range(max_rows):
        reference_text = (
            _normalize_viewport_line(reference_rows[row])
            if row < len(reference_rows)
            else ""
        )
        target_text = (
            _normalize_viewport_line(target_rows[row])
            if row < len(target_rows)
            else ""
        )
        if reference_text == target_text:
            continue
        rows.append(
            ViewportDeltaRecord(
                frame_index=frame_index,
                phase=phase,
                row=row,
                reference_label=reference_label,
                reference_text=reference_text,
                target_label=target_label,
                target_text=target_text,
            )
        )
    return rows


def _viewport_rows(record: dict[str, Any] | None) -> list[str] | None:
    if not isinstance(record, dict):
        return None
    viewport = record.get("viewport")
    if not isinstance(viewport, list):
        return None
    return [str(row) for row in viewport]


def _background_delta_record(
    *,
    frame_index: int,
    phase: str,
    reference_label: str,
    reference: dict[str, Any] | None,
    target_label: str,
    target: dict[str, Any] | None,
) -> BackgroundDeltaRecord | None:
    if phase != "final":
        return None
    reference_rows = _prompt_background_rows(reference)
    target_rows = _prompt_background_rows(target)
    if reference_rows == target_rows:
        return None
    if not reference_rows and not target_rows:
        return None
    return BackgroundDeltaRecord(
        frame_index=frame_index,
        phase=phase,
        metric="prompt_background_rows",
        reference_label=reference_label,
        reference_rows=reference_rows,
        target_label=target_label,
        target_rows=target_rows,
    )


def _prompt_background_rows(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    rows = record.get("prompt_background_rows")
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_index = row.get("row")
        columns = row.get("columns")
        bg = row.get("bg")
        if isinstance(row_index, int) and isinstance(columns, int) and isinstance(bg, str):
            normalized.append({"row": row_index, "columns": columns, "bg": bg})
    return normalized


def _normalize_viewport_line(line: str) -> str:
    normalized = line.rstrip()
    normalized = re.sub(
        r"^ (?:pipy|pi) v[0-9][0-9.]*$",
        " <product> v<version>",
        normalized,
    )
    normalized = re.sub(
        r"^ (?:Pipy|Pi) can explain its own features and look up its docs\. "
        r"Ask it how to use or extend (?:pipy|Pi)\.$",
        " <product> can explain its own features and look up its docs. "
        "Ask it how to use or extend <product>.",
        normalized,
    )
    normalized = re.sub(
        r"~/\.(?:pipy|pi/agent)/AGENTS\.md",
        "~/<agent-config>/AGENTS.md",
        normalized,
    )
    normalized = re.sub(
        r"↑[0-9.]+[kKmM]?\s+↓[0-9.]+[kKmM]?\s+\$[0-9.]+"
        r"\s+\(sub\)\s+[0-9.]+%/[0-9.]+[kKmM]?\s+\(auto\)",
        "<usage-meter>",
        normalized,
    )
    normalized = re.sub(
        r"↑[0-9.]+[kKmM]?\s+↓[0-9.]+[kKmM]?\s+R[0-9.]+[kKmM]?\s+\$[0-9.]+"
        r"\s+\(sub\)\s+[0-9.]+%/[0-9.]+[kKmM]?\s+\(auto\)",
        "<usage-meter>",
        normalized,
    )
    normalized = re.sub(
        r"\$[0-9.]+\s+\(sub\)\s+[0-9.]+%/[0-9.]+[kKmM]?\s+\(auto\)",
        "<usage-meter>",
        normalized,
    )
    normalized = re.sub(
        r"<usage-meter>\s+(\(openai-codex\))",
        r"<usage-meter> \1",
        normalized,
    )
    return normalized


def _write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_tsv(path: Path, records: list[DeltaRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "frame\tphase\tmetric\treference_label\treference_row\t"
        "reference_column\ttarget_label\ttarget_row\ttarget_column\t"
        "row_delta\tcolumn_delta\twithin_tolerance\tattributes_match\n"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        for record in records:
            handle.write(
                "\t".join(
                    (
                        str(record.frame_index),
                        record.phase,
                        record.metric,
                        record.reference_label,
                        _field(record.reference_row),
                        _field(record.reference_column),
                        record.target_label,
                        _field(record.target_row),
                        _field(record.target_column),
                        _field(record.row_delta),
                        _field(record.column_delta),
                        _field(record.within_tolerance),
                        _field(record.attributes_match),
                    )
                )
                + "\n"
            )


def _write_viewport_tsv(path: Path, records: list[ViewportDeltaRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "frame\tphase\trow\treference_label\treference_text\t"
        "target_label\ttarget_text\n"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        for record in records:
            handle.write(
                "\t".join(
                    (
                        str(record.frame_index),
                        record.phase,
                        str(record.row),
                        record.reference_label,
                        _tsv_text(record.reference_text),
                        record.target_label,
                        _tsv_text(record.target_text),
                    )
                )
                + "\n"
            )


def _write_anomalies(path: Path, anomalies: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("frame\tseverity\tmessage\n")
        for frame, severity, message in anomalies:
            handle.write(f"{frame}\t{severity}\t{message}\n")


def _field(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _tsv_text(value: str) -> str:
    return value.replace("\t", " ").replace("\n", " ")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare pipy and Pi screen metrics.")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-tsv", type=Path, required=True)
    parser.add_argument("--anomalies", type=Path, required=True)
    parser.add_argument("--reference-label", default="pipy")
    parser.add_argument("--target-label", default="pi")
    parser.add_argument("--max-row-delta", type=int, default=0)
    parser.add_argument("--max-column-delta", type=int, default=0)
    parser.add_argument("--viewport-json", type=Path)
    parser.add_argument("--viewport-tsv", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = compare_screen_metrics(
        reference_jsonl=args.reference,
        target_jsonl=args.target,
        out_json=args.out_json,
        out_tsv=args.out_tsv,
        anomalies_tsv=args.anomalies,
        reference_label=args.reference_label,
        target_label=args.target_label,
        max_row_delta=args.max_row_delta,
        max_column_delta=args.max_column_delta,
        viewport_json=args.viewport_json,
        viewport_tsv=args.viewport_tsv,
    )
    if args.report is not None:
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 1 if report["anomaly_count"] else 0


if __name__ == "__main__":  # pragma: no cover - exercised by script smoke
    raise SystemExit(_main())
