"""Small ANSI terminal screen model for TUI verification.

The verifier intentionally models screen cells, not just stripped text.
It is not a complete terminal emulator, but it covers the control
sequences pipy's TUI emits and tmux captures preserve: cursor moves,
line/screen clearing, SGR attributes, carriage returns, line feeds, and
alternate-screen entry/exit markers.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


_CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([ -/]*)([@-~])")
_OSC_END_RE = re.compile(r"[\x07]|\x1b\\")


@dataclass(frozen=True, slots=True)
class CellAttr:
    bold: bool = False
    dim: bool = False
    reverse: bool = False
    fg: str | None = None
    bg: str | None = None


@dataclass(slots=True)
class ScreenCell:
    char: str = " "
    attr: CellAttr = CellAttr()


@dataclass(frozen=True, slots=True)
class ScreenFinding:
    needle: str
    row: int
    column: int
    text: str
    attr: CellAttr
    cells: tuple[tuple[int, int], ...] = ()


@dataclass(slots=True)
class ScreenSnapshot:
    columns: int
    rows: int
    cursor_x: int
    cursor_y: int
    viewport_y: int
    cursor_visible: bool
    viewport: list[str]
    cells: list[list[ScreenCell]]

    def find(self, needle: str) -> list[ScreenFinding]:
        if not needle:
            return []
        findings: list[ScreenFinding] = []
        for row_index, line in enumerate(self.viewport):
            for column in range(len(line)):
                cells = self._matched_cells(row_index, column, needle)
                if cells is None:
                    continue
                attr = self.cells[row_index][column].attr
                findings.append(
                    ScreenFinding(
                        needle=needle,
                        row=row_index,
                        column=column,
                        text=self._matched_visible_text(row_index, column, len(needle)),
                        attr=attr,
                        cells=tuple(cells),
                    )
                )
        return findings

    def _matched_cells(
        self, row: int, column: int, needle: str
    ) -> list[tuple[int, int]] | None:
        row_index = row
        column_index = column
        cells: list[tuple[int, int]] = []
        for char in needle:
            while row_index < len(self.viewport) and column_index >= len(
                self.viewport[row_index]
            ):
                row_index += 1
                column_index = 0
                if row_index < len(self.viewport) and not self.viewport[row_index]:
                    return None
            if row_index >= len(self.viewport):
                return None
            if self.viewport[row_index][column_index] != char:
                return None
            cells.append((row_index, column_index))
            column_index += 1
        return cells

    def _matched_visible_text(self, row: int, column: int, length: int) -> str:
        chars: list[str] = []
        row_index = row
        column_index = column
        while len(chars) < length and row_index < len(self.viewport):
            if column_index >= len(self.viewport[row_index]):
                row_index += 1
                column_index = 0
                if row_index < len(self.viewport) and not self.viewport[row_index]:
                    break
                continue
            chars.append(self.viewport[row_index][column_index])
            column_index += 1
        return "".join(chars)

    def reverse_cells(self) -> list[dict[str, Any]]:
        cells: list[dict[str, Any]] = []
        for row_index, row in enumerate(self.cells):
            for column_index, cell in enumerate(row):
                if cell.attr.reverse:
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "char": cell.char,
                            "attr": asdict(cell.attr),
                        }
                    )
        return cells


class TerminalScreen:
    """A deterministic ANSI screen-cell model for verification tests."""

    def __init__(self, *, columns: int = 80, rows: int = 24) -> None:
        self.columns = max(1, columns)
        self.rows = max(1, rows)
        self.cursor_x = 0
        self.cursor_y = 0
        self.viewport_y = 0
        self.cursor_visible = True
        self._pending_wrap = False
        self._attr = CellAttr()
        self._cells = self._blank_screen()

    def write(self, data: str) -> None:
        index = 0
        while index < len(data):
            char = data[index]
            if char == "\x1b":
                index = self._handle_escape(data, index)
                continue
            if char == "\r":
                self.cursor_x = 0
                self._pending_wrap = False
                index += 1
                continue
            if char == "\n":
                self._line_feed()
                self._pending_wrap = False
                index += 1
                continue
            if char == "\b":
                self.cursor_x = max(0, self.cursor_x - 1)
                self._pending_wrap = False
                index += 1
                continue
            if char >= " " or char == "\t":
                if char == "\t":
                    spaces = 8 - (self.cursor_x % 8)
                    for _ in range(spaces):
                        self._put_char(" ")
                elif unicodedata.combining(char):
                    # Combining marks do not advance the cell model. The
                    # current TUI does not rely on them for verification.
                    pass
                else:
                    self._put_char(char)
                index += 1
                continue
            index += 1

    def snapshot(self) -> ScreenSnapshot:
        return ScreenSnapshot(
            columns=self.columns,
            rows=self.rows,
            cursor_x=self.cursor_x,
            cursor_y=self.cursor_y,
            viewport_y=self.viewport_y,
            cursor_visible=self.cursor_visible,
            viewport=[
                "".join(cell.char for cell in row).rstrip()
                for row in self._cells
            ],
            cells=[[ScreenCell(cell.char, cell.attr) for cell in row] for row in self._cells],
        )

    def _handle_escape(self, data: str, index: int) -> int:
        if data.startswith("\x1b[", index):
            match = _CSI_RE.match(data, index)
            if match is None:
                return index + 1
            params, _intermediate, final = match.groups()
            self._handle_csi(params, final)
            return match.end()
        if data.startswith("\x1b]", index):
            match = _OSC_END_RE.search(data, index + 2)
            return len(data) if match is None else match.end()
        # Single-character escape sequence or unsupported introducer.
        return min(len(data), index + 2)

    def _handle_csi(self, raw_params: str, final: str) -> None:
        private = raw_params.startswith("?")
        params = raw_params[1:] if private else raw_params
        values = _parse_csi_params(params)
        if final in {"H", "f"}:
            row = (values[0] if values else 1) or 1
            column = (values[1] if len(values) > 1 else 1) or 1
            self.cursor_y = min(self.rows - 1, max(0, row - 1))
            self.cursor_x = min(self.columns - 1, max(0, column - 1))
            self._pending_wrap = False
        elif final == "A":
            self.cursor_y = max(0, self.cursor_y - ((values[0] if values else 1) or 1))
            self._pending_wrap = False
        elif final == "B":
            self.cursor_y = min(
                self.rows - 1, self.cursor_y + ((values[0] if values else 1) or 1)
            )
            self._pending_wrap = False
        elif final == "C":
            self.cursor_x = min(
                self.columns - 1, self.cursor_x + ((values[0] if values else 1) or 1)
            )
            self._pending_wrap = False
        elif final == "D":
            self.cursor_x = max(0, self.cursor_x - ((values[0] if values else 1) or 1))
            self._pending_wrap = False
        elif final == "G":
            column = (values[0] if values else 1) or 1
            self.cursor_x = min(self.columns - 1, max(0, column - 1))
            self._pending_wrap = False
        elif final == "J":
            self._clear_screen(values[0] if values else 0)
        elif final == "K":
            self._clear_line(values[0] if values else 0)
        elif final == "m":
            self._set_sgr(values or [0])
        elif final in {"h", "l"} and private:
            self._set_private_mode(values, enabled=final == "h")

    def _set_private_mode(self, values: list[int], *, enabled: bool) -> None:
        for value in values:
            if value == 25:
                self.cursor_visible = enabled
            elif value == 1049 and enabled:
                self._cells = self._blank_screen()
                self.cursor_x = 0
                self.cursor_y = 0
                self.viewport_y = 0
                self._pending_wrap = False

    def _set_sgr(self, values: list[int]) -> None:
        index = 0
        attr = self._attr
        while index < len(values):
            value = values[index]
            if value == 0:
                attr = CellAttr()
            elif value == 1:
                attr = CellAttr(True, attr.dim, attr.reverse, attr.fg, attr.bg)
            elif value == 2:
                attr = CellAttr(attr.bold, True, attr.reverse, attr.fg, attr.bg)
            elif value == 7:
                attr = CellAttr(attr.bold, attr.dim, True, attr.fg, attr.bg)
            elif value == 22:
                attr = CellAttr(False, False, attr.reverse, attr.fg, attr.bg)
            elif value == 27:
                attr = CellAttr(attr.bold, attr.dim, False, attr.fg, attr.bg)
            elif value == 39:
                attr = CellAttr(attr.bold, attr.dim, attr.reverse, None, attr.bg)
            elif value == 49:
                attr = CellAttr(attr.bold, attr.dim, attr.reverse, attr.fg, None)
            elif value in {30, 31, 32, 33, 34, 35, 36, 37, 90, 91, 92, 93, 94, 95, 96, 97}:
                attr = CellAttr(attr.bold, attr.dim, attr.reverse, str(value), attr.bg)
            elif value in {40, 41, 42, 43, 44, 45, 46, 47, 100, 101, 102, 103, 104, 105, 106, 107}:
                attr = CellAttr(attr.bold, attr.dim, attr.reverse, attr.fg, str(value))
            elif value in {38, 48} and index + 4 < len(values) and values[index + 1] == 2:
                color = f"{values[index + 2]};{values[index + 3]};{values[index + 4]}"
                if value == 38:
                    attr = CellAttr(attr.bold, attr.dim, attr.reverse, color, attr.bg)
                else:
                    attr = CellAttr(attr.bold, attr.dim, attr.reverse, attr.fg, color)
                index += 4
            index += 1
        self._attr = attr

    def _put_char(self, char: str) -> None:
        if self._pending_wrap:
            self.cursor_x = 0
            self._line_feed()
            self._pending_wrap = False
        if self.cursor_y >= self.rows:
            self._line_feed()
        self._cells[self.cursor_y][self.cursor_x] = ScreenCell(char, self._attr)
        width = _cell_width(char)
        if self.cursor_x + width >= self.columns:
            self.cursor_x = self.columns - 1
            self._pending_wrap = True
        else:
            self.cursor_x += width

    def _line_feed(self) -> None:
        if self.cursor_y >= self.rows - 1:
            self._cells.pop(0)
            self._cells.append(self._blank_row())
            self.viewport_y += 1
        else:
            self.cursor_y += 1
        self._pending_wrap = False

    def _clear_screen(self, mode: int) -> None:
        if mode == 2:
            self._cells = self._blank_screen()
            return
        if mode == 0:
            self._clear_line(0)
            for row in range(self.cursor_y + 1, self.rows):
                self._cells[row] = self._blank_row()
        elif mode == 1:
            for row in range(0, self.cursor_y):
                self._cells[row] = self._blank_row()
            self._clear_line(1)

    def _clear_line(self, mode: int) -> None:
        if mode == 2:
            self._cells[self.cursor_y] = self._blank_row()
        elif mode == 1:
            for column in range(0, self.cursor_x + 1):
                self._cells[self.cursor_y][column] = ScreenCell()
        else:
            for column in range(self.cursor_x, self.columns):
                self._cells[self.cursor_y][column] = ScreenCell()

    def _blank_screen(self) -> list[list[ScreenCell]]:
        return [self._blank_row() for _ in range(self.rows)]

    def _blank_row(self) -> list[ScreenCell]:
        return [ScreenCell() for _ in range(self.columns)]


def parse_ansi_screen(data: str, *, columns: int | None = None, rows: int | None = None) -> ScreenSnapshot:
    plain = strip_ansi(data)
    plain_rows = plain.splitlines() or [""]
    effective_columns = columns or max(1, max(len(line) for line in plain_rows))
    effective_rows = rows or max(1, len(plain_rows))
    screen = TerminalScreen(columns=effective_columns, rows=effective_rows)
    screen.write(data)
    return screen.snapshot()


def strip_ansi(data: str) -> str:
    screen = TerminalScreen(columns=10000, rows=max(1, data.count("\n") + 1))
    # Avoid using the full model for stripping when very long captures are
    # analyzed; regex stripping keeps original newlines and is enough here.
    del screen
    data = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", data)
    data = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", data)
    data = re.sub(r"\x1b.", "", data)
    return data


def analyze_frame_files(
    *,
    frames_dir: Path,
    cursor_metrics_path: Path | None,
    prompt: str = "",
    expected_output: str | None = None,
    columns: int | None = None,
    rows: int | None = None,
    out_jsonl: Path,
    report_json: Path,
    anomalies_tsv: Path,
) -> dict[str, Any]:
    cursor_metrics = _read_cursor_metrics(cursor_metrics_path)
    frame_paths = sorted(frames_dir.glob("frame-*.ansi"))
    records: list[dict[str, Any]] = []
    anomalies: list[tuple[str, str, str]] = []

    for path in frame_paths:
        frame, phase = _frame_identity(path)
        data = path.read_bytes().decode("utf-8", errors="replace")
        # tmux capture-pane writes a static viewport, so line feeds in
        # captured files represent complete rows. Preserve CR in real
        # renderer streams, but normalize LF-only captures to CRLF so the
        # screen model does not invent diagonal drift while replaying them.
        replay_data = data if "\r" in data else data.replace("\n", "\r\n")
        snapshot = parse_ansi_screen(replay_data, columns=columns, rows=rows)
        prompt_findings = snapshot.find(prompt)
        prompt_background_rows = _background_rows(
            snapshot,
            (
                prompt_findings[0].attr.bg
                if prompt_findings and prompt_findings[0].attr.bg
                else None
            ),
        )
        findings = {
            "prompt": [asdict(finding) for finding in prompt_findings],
            "working": [asdict(finding) for finding in snapshot.find("Working...")],
            "status": [
                asdict(finding)
                for finding in snapshot.find("(openai-codex) gpt-5.5")
            ],
            "cwd": [asdict(finding) for finding in snapshot.find("~/projects/pipy")],
            "footer_meter": [
                asdict(finding)
                for finding in snapshot.find("$0.000 (sub) 0.0%/272k")
            ],
        }
        if expected_output:
            findings["expected_output"] = [
                asdict(finding)
                for finding in snapshot.find(expected_output)
                if not _overlaps_prompt_finding(finding, prompt_findings)
            ]
        separator_rows = _separator_rows(snapshot.viewport)
        input_row = _input_row(separator_rows)
        reverse_cells = snapshot.reverse_cells()
        live_cursor = cursor_metrics.get((frame, phase))
        cursor_match = _cursor_matches(
            live_cursor=live_cursor,
            input_row=input_row,
            reverse_cells=reverse_cells,
        )
        record = {
            "frame": frame,
            "phase": phase,
            "path": str(path),
            "prompt": prompt,
            "columns": snapshot.columns,
            "rows": snapshot.rows,
            "viewport": snapshot.viewport,
            "screen_cursor": {
                "x": snapshot.cursor_x,
                "y": snapshot.cursor_y,
                "visible": snapshot.cursor_visible,
            },
            "live_cursor": live_cursor,
            "viewport_y": snapshot.viewport_y,
            "separator_rows": separator_rows,
            "inferred_input_row": input_row,
            "prompt_background_rows": prompt_background_rows,
            "visual_regions": _visual_regions(
                snapshot,
                prompt_findings=prompt_findings,
                separator_rows=separator_rows,
            ),
            "reverse_cells": reverse_cells,
            "cursor_matches_input_row": cursor_match,
            "expected_output": expected_output,
            "findings": findings,
        }
        records.append(record)
        _collect_anomalies(record, anomalies)

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    summary = {
        "frames": len(records),
        "anomaly_count": len(anomalies),
        "prompt": prompt,
        "expected_output": expected_output,
        "screen_metrics": str(out_jsonl),
        "anomalies": str(anomalies_tsv),
    }
    report_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with anomalies_tsv.open("w", encoding="utf-8") as handle:
        handle.write("frame\tseverity\tmessage\n")
        for frame_label, severity, message in anomalies:
            handle.write(f"{frame_label}\t{severity}\t{message}\n")
    return summary


def _collect_anomalies(record: dict[str, Any], anomalies: list[tuple[str, str, str]]) -> None:
    frame = f"{record['frame']}:{record['phase']}"
    phase = str(record["phase"])
    findings = record["findings"]
    if (phase.startswith("active") or phase == "final") and record.get("prompt"):
        prompt_count = len(findings["prompt"])
        if prompt_count != 1:
            anomalies.append(
                (frame, "error", f"submitted prompt visible count is {prompt_count}")
            )
        elif record.get("rows", 0) >= 20 and findings["prompt"][0]["row"] == 0:
            anomalies.append((frame, "error", "submitted prompt is pinned to top row"))
    if len(findings["working"]) > 1:
        anomalies.append((frame, "error", "duplicate Working... rows"))
    if phase == "final" and findings["working"]:
        anomalies.append((frame, "error", "stale Working... row on final frame"))
    if phase == "final" and record.get("expected_output"):
        if not findings.get("expected_output"):
            anomalies.append((frame, "error", "expected model output is not visible"))
    if len(record["reverse_cells"]) > 1:
        anomalies.append((frame, "error", "multiple reverse cursor cells visible"))
    if not findings["status"]:
        anomalies.append((frame, "error", "missing openai-codex gpt-5.5 status footer"))
    if phase.startswith("active") and record["inferred_input_row"] is None:
        anomalies.append((frame, "error", "could not infer pinned input row from separators"))
    if phase.startswith("active") and record["cursor_matches_input_row"] is False:
        anomalies.append((frame, "error", "live cursor does not match drawn input cursor"))


def _cursor_matches(
    *,
    live_cursor: dict[str, Any] | None,
    input_row: int | None,
    reverse_cells: list[dict[str, Any]],
) -> bool | None:
    if live_cursor is None or input_row is None or not reverse_cells:
        return None
    candidates = [
        cell
        for cell in reverse_cells
        if cell["row"] in {input_row, input_row + 1, max(0, input_row - 1)}
    ]
    if not candidates:
        return False
    live_x = live_cursor.get("cursor_x")
    live_y = live_cursor.get("cursor_y")
    return any(
        live_x == cell["column"]
        and live_y in {cell["row"] - 1, cell["row"], cell["row"] + 1}
        for cell in candidates
    )


def _overlaps_prompt_finding(
    finding: ScreenFinding, prompt_findings: list[ScreenFinding]
) -> bool:
    cells = set(finding.cells)
    if not cells:
        return False
    return any(cells.intersection(prompt.cells) for prompt in prompt_findings)


def _background_rows(snapshot: ScreenSnapshot, bg: str | None) -> list[dict[str, Any]]:
    if not bg:
        return []
    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(snapshot.cells):
        columns = sum(1 for cell in row if cell.attr.bg == bg)
        if columns:
            rows.append({"row": row_index, "columns": columns, "bg": bg})
    return rows


def _visual_regions(
    snapshot: ScreenSnapshot,
    *,
    prompt_findings: list[ScreenFinding],
    separator_rows: list[int],
) -> dict[str, list[dict[str, Any]]]:
    """Return style-sensitive rows for Pi/pipy visual parity comparison.

    The comparison verifier needs stronger evidence than screenshot text: it
    must fail when important rows keep their text but lose the background,
    foreground, reverse-cursor, or dim/bold attributes. Regions are named by
    product semantics so expected dynamic text can still vary between runs.
    """

    prompt_bg = (
        prompt_findings[0].attr.bg
        if prompt_findings and prompt_findings[0].attr.bg
        else None
    )
    regions: dict[str, list[dict[str, Any]]] = {
        "submitted_prompt": [],
        "tool_call": [],
        "tool_result": [],
        "slash_menu": [],
        "slash_menu_selection": [],
        "separator": [],
        "cursor": [],
        "footer": [],
    }
    footer_rows: set[int] = set()
    if separator_rows:
        footer_start = separator_rows[-1] + 1
        footer_rows = {footer_start, footer_start + 1}
    for row_index, row in enumerate(snapshot.cells):
        text = snapshot.viewport[row_index] if row_index < len(snapshot.viewport) else ""
        stripped = text.strip()
        summary = _row_style_summary(row_index, text, row)
        if row_index in separator_rows:
            regions["separator"].append(summary)
        if _row_has_reverse(row):
            regions["cursor"].append(summary)
        if prompt_bg and _row_has_bg(row, prompt_bg):
            regions["submitted_prompt"].append(summary)
            continue
        if _row_has_bg(row, "40;50;40") or _row_has_bg(row, "28;42;30"):
            if stripped.startswith("$ "):
                regions["tool_call"].append(summary)
            else:
                regions["tool_result"].append(summary)
            continue
        if _looks_like_slash_menu_row(stripped):
            if stripped.startswith("→") or _row_has_bg(row, "52;53;65"):
                regions["slash_menu_selection"].append(summary)
            else:
                regions["slash_menu"].append(summary)
            continue
        if row_index in footer_rows:
            regions["footer"].append(summary)
    return {key: value for key, value in regions.items() if value}


def _row_style_summary(
    row_index: int,
    text: str,
    row: list[ScreenCell],
) -> dict[str, Any]:
    return {
        "row": row_index,
        "text": text.rstrip(),
        "bg": _dominant_attr(row, "bg"),
        "fg": _dominant_attr(row, "fg"),
        "reverse_columns": sum(1 for cell in row if cell.attr.reverse),
        "dim_columns": sum(1 for cell in row if cell.attr.dim),
        "bold_columns": sum(1 for cell in row if cell.attr.bold),
    }


def _dominant_attr(row: list[ScreenCell], attr_name: str) -> str | None:
    counts: dict[str, int] = {}
    for cell in row:
        value = getattr(cell.attr, attr_name)
        if not isinstance(value, str):
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _row_has_bg(row: list[ScreenCell], bg: str) -> bool:
    return any(cell.attr.bg == bg for cell in row)


def _row_has_reverse(row: list[ScreenCell]) -> bool:
    return any(cell.attr.reverse for cell in row)


def _looks_like_slash_menu_row(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith("→"):
        stripped = stripped[1:].strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        command = stripped.split(maxsplit=1)[0]
    else:
        command = "/" + stripped.split(maxsplit=1)[0]
    if command == "/":
        return False
    return command in {
        "/help",
        "/clear",
        "/status",
        "/settings",
        "/copy",
        "/login",
        "/logout",
        "/model",
        "/read",
        "/ask-file",
        "/propose-file",
        "/apply-proposal",
        "/exit",
        "/quit",
    }


def _separator_rows(rows: Iterable[str]) -> list[int]:
    result: list[int] = []
    for index, row in enumerate(rows):
        stripped = row.strip()
        if len(stripped) >= 10 and set(stripped) == {"─"}:
            result.append(index)
    return result


def _input_row(separator_rows: list[int]) -> int | None:
    for previous, current in zip(separator_rows, separator_rows[1:]):
        if current - previous == 2:
            return previous + 1
    if separator_rows:
        return separator_rows[0] + 1
    return None


def _read_cursor_metrics(path: Path | None) -> dict[tuple[int, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if index == 0 or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        frame = int(parts[0])
        phase = parts[1]
        rows[(frame, phase)] = {
            "cursor_x": _int_or_none(parts[2]),
            "cursor_y": _int_or_none(parts[3]),
            "pane_active": parts[4] == "1",
        }
    return rows


def _frame_identity(path: Path) -> tuple[int, str]:
    match = re.match(r"frame-(\d+)-(.+)\.ansi$", path.name)
    if match is None:
        return (0, path.stem)
    return int(match.group(1)), match.group(2)


def _parse_csi_params(params: str) -> list[int]:
    if params == "":
        return []
    values: list[int] = []
    for part in params.split(";"):
        values.append(0 if part == "" else _int_or_none(part) or 0)
    return values


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _cell_width(char: str) -> int:
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze tmux ANSI frames as screen cells."
    )
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("--cursor-metrics", type=Path)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--expected-output")
    parser.add_argument("--columns", type=int)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--out-jsonl", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--anomalies", type=Path, required=True)
    args = parser.parse_args(argv)
    analyze_frame_files(
        frames_dir=args.frames_dir,
        cursor_metrics_path=args.cursor_metrics,
        prompt=args.prompt,
        expected_output=args.expected_output,
        columns=args.columns,
        rows=args.rows,
        out_jsonl=args.out_jsonl,
        report_json=args.report,
        anomalies_tsv=args.anomalies,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through script smoke
    raise SystemExit(_main())
