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
    viewport: list[str] | None = None,
    prompt_background_rows: list[dict] | None = None,
) -> dict:
    record = {
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
    if viewport is not None:
        record["viewport"] = viewport
    if prompt_background_rows is not None:
        record["prompt_background_rows"] = prompt_background_rows
    return record


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


def test_compare_screen_metrics_reports_final_prompt_background_mismatch(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    common = [
        {"row": 10, "columns": 100, "bg": "52;53;65"},
        {"row": 11, "columns": 100, "bg": "52;53;65"},
        {"row": 12, "columns": 100, "bg": "52;53;65"},
    ]
    _write_jsonl(
        reference,
        [
            _record(
                prompt_background_rows=[
                    *common,
                    {"row": 13, "columns": 100, "bg": "52;53;65"},
                ]
            )
        ],
    )
    _write_jsonl(target, [_record(prompt_background_rows=common)])

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
    )

    assert report["final_prompt_background_delta_count"] == 1
    assert report["anomaly_count"] == 1
    anomalies = (tmp_path / "anomalies.tsv").read_text(encoding="utf-8")
    assert "prompt_background_rows differ" in anomalies
    assert "'row': 13" in anomalies


def test_compare_screen_metrics_reports_final_viewport_mismatch(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    _write_jsonl(
        reference,
        [
            _record(
                viewport=[
                    "[Skills]",
                    " hello world",
                    "Hello! How can I help?",
                    "$0.004 (sub) 1.1%/272k (auto)",
                ]
            )
        ],
    )
    _write_jsonl(
        target,
        [
            _record(
                viewport=[
                    "[Context]",
                    " hello world",
                    "Hello!",
                    "$0.015 (sub) 1.1%/272k (auto)",
                ]
            )
        ],
    )

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
        viewport_json=tmp_path / "viewport-deltas.json",
        viewport_tsv=tmp_path / "viewport-deltas.tsv",
    )

    assert report["final_viewport_delta_count"] == 2
    assert report["anomaly_count"] == 3
    anomalies = (tmp_path / "anomalies.tsv").read_text(encoding="utf-8")
    assert "final viewport differs on 2 rows" in anomalies
    assert "Hello! How can I help?" in anomalies
    viewport_deltas = json.loads(
        (tmp_path / "viewport-deltas.json").read_text(encoding="utf-8")
    )
    assert [delta["row"] for delta in viewport_deltas] == [0, 2]


def test_compare_screen_metrics_normalizes_reasoning_usage_meter(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "pipy.jsonl"
    target = tmp_path / "pi.jsonl"
    _write_jsonl(
        reference,
        [
            _record(
                viewport=[
                    " Hello!",
                    "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high",
                ]
            )
        ],
    )
    _write_jsonl(
        target,
        [
            _record(
                viewport=[
                    " Hello!",
                    "↑410 ↓6 R2.6k $0.010 (sub) 1.1%/272k (auto) (openai-codex) gpt-5.5 • high",
                ]
            )
        ],
    )

    report = compare_screen_metrics(
        reference_jsonl=reference,
        target_jsonl=target,
        out_json=tmp_path / "deltas.json",
        out_tsv=tmp_path / "deltas.tsv",
        anomalies_tsv=tmp_path / "anomalies.tsv",
        viewport_json=tmp_path / "viewport-deltas.json",
        viewport_tsv=tmp_path / "viewport-deltas.tsv",
    )

    assert report["final_viewport_delta_count"] == 0
    assert report["anomaly_count"] == 0
