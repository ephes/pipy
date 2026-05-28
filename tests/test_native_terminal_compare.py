"""Pi-vs-pipy screen-metric comparison tests."""

from __future__ import annotations

import json
from pathlib import Path

from pipy_harness.native.terminal_compare import compare_screen_metrics


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _record(
    *,
    phase: str = "final",
    prompt_row: int = 15,
    output_row: int = 18,
    status_row: int = 24,
    input_row: int = 21,
    cursor_y: int = 22,
    cursor_x: int = 0,
) -> dict:
    return {
        "phase": phase,
        "findings": {
            "prompt": [{"row": prompt_row, "column": 1, "attr": {"fg": "white"}}],
            "expected_output": [
                {"row": output_row, "column": 1, "attr": {"fg": None}}
            ],
            "status": [{"row": status_row, "column": 71, "attr": {"fg": "dim"}}],
            "cwd": [{"row": status_row - 1, "column": 0, "attr": {"fg": "dim"}}],
        },
        "inferred_input_row": input_row,
        "live_cursor": {"cursor_y": cursor_y, "cursor_x": cursor_x},
        "reverse_cells": [
            {
                "row": input_row,
                "column": cursor_x,
                "attr": {"reverse": True, "fg": None, "bg": None},
            }
        ],
    }


def test_compare_screen_metrics_writes_row_column_delta_artifacts(tmp_path: Path) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    _write_jsonl(reference, [_record()])
    _write_jsonl(target, [_record(output_row=19, cursor_y=23)])

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
        max_row_delta=1,
    )

    assert report["compared_frames"] == 1
    assert report["anomaly_count"] == 0
    deltas = json.loads((tmp_path / "deltas.json").read_text(encoding="utf-8"))
    expected_output = next(
        delta for delta in deltas if delta["metric"] == "expected_output"
    )
    assert expected_output["row_delta"] == 1
    assert expected_output["column_delta"] == 0
    assert expected_output["within_tolerance"] is True
    assert expected_output["attributes_match"] is True
    assert "live_cursor" in (tmp_path / "deltas.tsv").read_text(encoding="utf-8")


def test_compare_screen_metrics_reports_out_of_tolerance_delta(tmp_path: Path) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    _write_jsonl(reference, [_record()])
    _write_jsonl(target, [_record(prompt_row=25)])

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
        max_row_delta=2,
    )

    assert report["anomaly_count"] == 1
    assert "prompt delta row=10" in (tmp_path / "anomalies.tsv").read_text(
        encoding="utf-8"
    )


def test_compare_screen_metrics_pairs_last_final_frame_by_phase(tmp_path: Path) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    _write_jsonl(
        reference,
        [
            _record(phase="active"),
            _record(phase="final", output_row=18),
        ],
    )
    _write_jsonl(
        target,
        [
            _record(phase="active"),
            _record(phase="active"),
            _record(phase="active"),
            _record(phase="final", output_row=19),
        ],
    )

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
        max_row_delta=1,
    )

    assert report["compared_frames"] == 2
    deltas = json.loads((tmp_path / "deltas.json").read_text(encoding="utf-8"))
    final_output = next(
        delta
        for delta in deltas
        if delta["phase"] == "final" and delta["metric"] == "expected_output"
    )
    assert final_output["row_delta"] == 1
    assert final_output["within_tolerance"] is True


def test_compare_screen_metrics_reports_attribute_mismatch(tmp_path: Path) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    target_record = _record()
    target_record["findings"]["expected_output"][0]["attr"] = {"fg": "128;128;128"}
    _write_jsonl(reference, [_record()])
    _write_jsonl(target, [target_record])

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
    )

    assert report["anomaly_count"] == 1
    assert "expected_output cell attributes differ" in (
        tmp_path / "anomalies.tsv"
    ).read_text(encoding="utf-8")
    deltas = json.loads((tmp_path / "deltas.json").read_text(encoding="utf-8"))
    expected_output = next(
        delta for delta in deltas if delta["metric"] == "expected_output"
    )
    assert expected_output["attributes_match"] is False


def test_compare_screen_metrics_reports_missing_required_final_metric(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    missing_output = _record()
    missing_output["findings"]["expected_output"] = []
    _write_jsonl(reference, [_record()])
    _write_jsonl(target, [missing_output])

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
    )

    assert report["anomaly_count"] == 1
    assert "expected_output missing on one side of comparison" in (
        tmp_path / "anomalies.tsv"
    ).read_text(encoding="utf-8")
