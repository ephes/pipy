"""ANSI screen-cell verification for the native tool-loop TUI."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.terminal_screen import (
    TerminalScreen,
    analyze_frame_files,
    parse_ansi_screen,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


class _TtyBuffer:
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def _ui(tmp_path: Path) -> tuple[ToolLoopTerminalUi, _TtyBuffer]:
    terminal = _TtyBuffer()
    return (
        ToolLoopTerminalUi(
            input_stream=cast(TextIO, io.StringIO()),
            terminal_stream=cast(TextIO, terminal),
            cwd=tmp_path,
        ),
        terminal,
    )


def test_terminal_screen_tracks_cursor_clear_and_cell_attributes() -> None:
    screen = TerminalScreen(columns=12, rows=4)

    screen.write("\x1b[2J\x1b[Hplain")
    screen.write("\x1b[2;3H\x1b[1;38;2;1;2;3;48;2;4;5;6mX\x1b[0m")
    screen.write("\x1b[4;1Htail\x1b[2K")
    snapshot = screen.snapshot()

    assert snapshot.viewport[0] == "plain"
    assert snapshot.find("X")[0].row == 1
    attr = snapshot.find("X")[0].attr
    assert attr.bold is True
    assert attr.fg == "1;2;3"
    assert attr.bg == "4;5;6"
    assert snapshot.viewport[3] == ""


def test_terminal_screen_finds_visible_text_wrapped_across_rows() -> None:
    screen = TerminalScreen(columns=20, rows=4)

    screen.write(
        "\x1b[38;2;212;212;212m Reply exactly: This\r\n"
        " prompt wraps"
    )
    snapshot = screen.snapshot()

    findings = snapshot.find("Reply exactly: This prompt wraps")
    assert len(findings) == 1
    assert findings[0].row == 0
    assert findings[0].column == 1
    assert findings[0].attr.fg == "212;212;212"


def test_terminal_screen_does_not_match_across_blank_rows() -> None:
    snapshot = parse_ansi_screen("alpha\r\n\r\nbeta", columns=12, rows=3)

    assert snapshot.find("alphabeta") == []


def test_parse_tui_paint_locates_prompt_footer_and_drawn_cursor(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui, terminal = _ui(tmp_path)
    ui.footer_lines = ("~/projects/pipy (main)", "$0.000 (sub) 0.0%/272k (auto)")
    ui.submit_user_message("visible prompt")
    ui.append_assistant("visible answer")
    ui.input_text = "next"

    ui.paint()

    width, height = ui._dimensions()
    snapshot = parse_ansi_screen(terminal.getvalue(), columns=width, rows=height)

    assert len(snapshot.find("visible prompt")) == 1
    assert len(snapshot.find("visible answer")) == 1
    assert len(snapshot.find("$0.000 (sub) 0.0%/272k")) == 1
    reverse_cells = snapshot.reverse_cells()
    assert any(cell["char"] == " " and cell["column"] == 4 for cell in reverse_cells)
    assert snapshot.cursor_x == 4
    assert snapshot.cursor_y == next(
        index
        for index, line in enumerate(snapshot.viewport)
        if line.startswith("next")
    )


def test_analyze_frame_files_writes_machine_readable_cursor_and_visibility(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    frame = (
        "\x1b[48;2;52;53;65m user prompt\x1b[0m\r\n"
        "\x1b[48;2;52;53;65mvisible answer\x1b[0m\r\n"
        "────────────\r\n"
        "\x1b[39m\x1b[7m \x1b[0m\r\n"
        "────────────\r\n"
        "~/projects/pipy (main)\r\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-active.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tactive\t0\t3\t1\n",
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "screen-metrics.jsonl"
    report = tmp_path / "terminal-report.json"
    anomalies = tmp_path / "screen-anomalies.tsv"

    summary = analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt="user prompt",
        expected_output="visible answer",
        out_jsonl=out_jsonl,
        report_json=report,
        anomalies_tsv=anomalies,
    )

    record = json.loads(out_jsonl.read_text(encoding="utf-8"))
    assert summary["frames"] == 1
    assert record["viewport"][0] == " user prompt"
    assert record["findings"]["prompt"][0]["row"] == 0
    assert record["findings"]["expected_output"][0]["row"] == 1
    assert record["findings"]["status"][0]["row"] == 6
    assert record["prompt_background_rows"] == [
        {"bg": "52;53;65", "columns": 12, "row": 0},
        {"bg": "52;53;65", "columns": 14, "row": 1},
    ]
    assert record["visual_regions"]["submitted_prompt"][0]["bg"] == "52;53;65"
    assert record["visual_regions"]["separator"][0]["row"] == 2
    assert record["visual_regions"]["cursor"][0]["reverse_columns"] == 1
    assert record["visual_regions"]["footer"][1]["text"].endswith("gpt-5.5 • high")
    assert record["inferred_input_row"] == 3
    assert record["cursor_matches_input_row"] is True
    assert "visible count" not in anomalies.read_text(encoding="utf-8")


def test_analyze_frame_files_treats_tmux_lf_rows_as_static_viewport(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    frame = (
        " user prompt\n"
        "────────────\n"
        "\x1b[7m \x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-active.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tactive\t0\t2\t1\n",
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "screen-metrics.jsonl"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt="user prompt",
        expected_output="gpt-5.5",
        out_jsonl=out_jsonl,
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=tmp_path / "screen-anomalies.tsv",
    )

    record = json.loads(out_jsonl.read_text(encoding="utf-8"))
    assert record["findings"]["prompt"][0]["row"] == 0
    assert record["findings"]["cwd"][0]["row"] == 4
    assert record["findings"]["status"][0]["row"] == 5
    assert record["cursor_matches_input_row"] is True


def test_analyze_frame_files_infers_input_row_when_slash_menu_is_open(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    frame = (
        "────────────\n"
        "/\x1b[7m \x1b[0m\n"
        "\x1b[38;2;138;190;183m→ help          Show keyboard shortcuts (alias of /hotkeys)\x1b[0m\n"
        "  clear\x1b[38;2;128;128;128m         Deprecated: use /new (start a new session)\x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-slash-open.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tslash-open\t1\t1\t1\n",
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "screen-metrics.jsonl"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        out_jsonl=out_jsonl,
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=tmp_path / "screen-anomalies.tsv",
    )

    record = json.loads(out_jsonl.read_text(encoding="utf-8"))
    assert record["inferred_input_row"] == 1
    assert record["visual_regions"]["slash_menu_selection"][0]["text"].startswith(
        "→ help"
    )
    assert record["visual_regions"]["slash_menu"][0]["text"].startswith("  clear")


def test_analyze_frame_files_counts_wrapped_prompt_once(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    prompt = "Reply exactly: This prompt wraps across terminal rows"
    frame = (
        " Reply exactly: This prompt wraps across\n"
        " terminal rows\n"
        " visible answer\n"
        "────────────\n"
        "\x1b[7m \x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-final.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tfinal\t0\t4\t1\n",
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "screen-metrics.jsonl"
    anomalies = tmp_path / "screen-anomalies.tsv"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt=prompt,
        expected_output="visible answer",
        out_jsonl=out_jsonl,
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=anomalies,
    )

    record = json.loads(out_jsonl.read_text(encoding="utf-8"))
    assert record["findings"]["prompt"][0]["row"] == 0
    assert record["findings"]["prompt"][0]["column"] == 1
    assert "visible count" not in anomalies.read_text(encoding="utf-8")


def test_analyze_frame_files_excludes_expected_output_inside_wrapped_prompt(
    tmp_path: Path,
) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    prompt = "Reply exactly: stable words with marker WRAP DONE"
    frame = (
        " Reply exactly: stable words with marker\n"
        " WRAP DONE\n"
        " WRAP DONE\n"
        "────────────\n"
        "\x1b[7m \x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-final.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tfinal\t0\t4\t1\n",
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "screen-metrics.jsonl"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt=prompt,
        expected_output="WRAP DONE",
        out_jsonl=out_jsonl,
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=tmp_path / "screen-anomalies.tsv",
    )

    record = json.loads(out_jsonl.read_text(encoding="utf-8"))
    assert len(record["findings"]["expected_output"]) == 1
    assert record["findings"]["expected_output"][0]["row"] == 2


def test_analyze_frame_files_reports_core_tui_regressions(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    frame = (
        "user prompt\n"
        "⠋ Working...\n"
        "⠙ Working...\n"
        "────────────\n"
        "\x1b[7m \x1b[0m\n"
        "\x1b[7m \x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-final.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tfinal\t10\t0\t1\n",
        encoding="utf-8",
    )
    anomalies = tmp_path / "screen-anomalies.tsv"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt="user prompt",
        expected_output="missing answer",
        columns=80,
        rows=12,
        out_jsonl=tmp_path / "screen-metrics.jsonl",
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=anomalies,
    )

    text = anomalies.read_text(encoding="utf-8")
    assert "duplicate Working... rows" in text
    assert "stale Working... row on final frame" in text
    assert "expected model output is not visible" in text
    assert "multiple reverse cursor cells visible" in text


def test_analyze_frame_files_reports_prompt_pinned_to_top_row(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    frame = (
        "user prompt\n"
        "\n"
        "visible answer\n"
        + "\n" * 18
        + "────────────\n"
        "\x1b[7m \x1b[0m\n"
        "────────────\n"
        "~/projects/pipy (main)\n"
        "$0.000 (sub) 0.0%/272k (auto) (openai-codex) gpt-5.5 • high"
    )
    (frames / "frame-001-final.ansi").write_text(frame, encoding="utf-8")
    cursor_metrics = tmp_path / "cursor-metrics.tsv"
    cursor_metrics.write_text(
        "frame\tphase\tcursor_x\tcursor_y\tpane_active\n"
        "1\tfinal\t0\t22\t1\n",
        encoding="utf-8",
    )
    anomalies = tmp_path / "screen-anomalies.tsv"

    analyze_frame_files(
        frames_dir=frames,
        cursor_metrics_path=cursor_metrics,
        prompt="user prompt",
        expected_output="visible answer",
        columns=80,
        rows=30,
        out_jsonl=tmp_path / "screen-metrics.jsonl",
        report_json=tmp_path / "terminal-report.json",
        anomalies_tsv=anomalies,
    )

    assert "submitted prompt is pinned to top row" in anomalies.read_text(
        encoding="utf-8"
    )
