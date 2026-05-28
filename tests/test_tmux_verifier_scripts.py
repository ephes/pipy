from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tmux_verifiers_accept_env_configured_pane_geometry() -> None:
    for relative in (
        "scripts/tmux_transient_ui_verify.sh",
        "scripts/tmux_pi_comparison_verify.sh",
        "scripts/tmux_tui_input_verify.sh",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")

        assert 'PANE_COLUMNS="${PANE_COLUMNS:-100}"' in text
        assert 'PANE_ROWS="${PANE_ROWS:-30}"' in text
        assert "printf 'pane_columns\\t%s\\n' \"$PANE_COLUMNS\"" in text
        assert "printf 'pane_rows\\t%s\\n' \"$PANE_ROWS\"" in text
