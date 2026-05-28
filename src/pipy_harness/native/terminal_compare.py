"""Compare pipy and Pi terminal-screen verification artifacts."""

from __future__ import annotations

import argparse
import json
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
) -> dict[str, Any]:
    """Compare two screen-metrics JSONL files and write delta artifacts."""

    reference_records = _load_records(reference_jsonl)
    target_records = _load_records(target_jsonl)
    pairs = _comparison_pairs(reference_records, target_records)
    deltas: list[DeltaRecord] = []
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

    report = {
        "reference_label": reference_label,
        "target_label": target_label,
        "reference_metrics": str(reference_jsonl),
        "target_metrics": str(target_jsonl),
        "compared_frames": len(pairs),
        "delta_count": len(deltas),
        "anomaly_count": len(anomalies),
        "max_row_delta": max_row_delta,
        "max_column_delta": max_column_delta,
    }
    _write_json(out_json, [asdict(delta) for delta in deltas])
    _write_tsv(out_tsv, deltas)
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


def _write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    )
    if args.report is not None:
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by script smoke
    raise SystemExit(_main())
