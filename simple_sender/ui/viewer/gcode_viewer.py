#!/usr/bin/env python3
# Simple Sender (GRBL G-code Sender)
# Copyright (C) 2026 Bob Kolbasowski
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Optional (not required by the license): If you make improvements, please consider
# contributing them back upstream (e.g., via a pull request) so others can benefit.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Headless live G-code state helpers used by the current runtime."""

from __future__ import annotations

from ...utils.constants import (
    GCODE_LIVE_WINDOW_LOOKAHEAD_LINES,
    GCODE_LIVE_WINDOW_PAST_LINES,
)


def reset_gcode_view_for_run(app) -> None:
    if not hasattr(app, "gview"):
        return
    app._clear_pending_ui_updates()
    app.gview.clear_highlights()
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1


class HeadlessGcodeView:
    """Non-widget live-window/job-view state holder used by the current runtime.

    The redesigned lower UI no longer exposes a visible G-code pane. Runtime
    code still expects ``app.gview`` for lightweight job-present checks,
    load/reset state, and current-line bookkeeping, so this headless object
    keeps those semantics without a widget tree.
    """

    def __init__(self) -> None:
        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self._live_payload: dict[str, object] | None = None

    def apply_theme_palette(self, _palette: dict[str, str] | None) -> None:
        return None

    def set_live_window(
        self,
        past_lines: list[tuple[int, str]],
        current_line: tuple[int, str] | None,
        next_lines: list[tuple[int, str]],
        *,
        next_buffered_count: int = 0,
        highlight_current: bool = True,
    ) -> None:
        current_idx: int | None = None
        if isinstance(current_line, tuple) and len(current_line) >= 1:
            try:
                current_idx = int(current_line[0])
            except Exception:
                current_idx = None

        filtered_past: list[tuple[int, str]] = []
        for idx, raw in past_lines:
            try:
                idx_i = int(idx)
            except Exception:
                continue
            if current_idx is not None and idx_i == current_idx:
                continue
            filtered_past.append((idx_i, str(raw or "")))
        past = list(filtered_past[-int(GCODE_LIVE_WINDOW_PAST_LINES):])
        look_ahead = list(next_lines[: int(GCODE_LIVE_WINDOW_LOOKAHEAD_LINES)])

        line_count = 2
        line_count += max(1, int(len(look_ahead)))
        line_count += 2
        line_count += int(len(past))
        self.lines_count = int(line_count)
        self._live_payload = {
            "past_lines": list(past),
            "current_line": current_line,
            "next_lines": list(look_ahead),
            "next_buffered_count": int(next_buffered_count),
            "highlight_current": bool(highlight_current),
        }
        if highlight_current and current_idx is not None:
            self._current_idx = int(current_idx)

    def clear(self) -> None:
        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self._live_payload = None

    def clear_highlights(self) -> None:
        self._current_idx = -1

    def mark_sent_upto(self, idx: int) -> None:
        self._sent_upto = int(idx)

    def mark_acked_upto(self, idx: int) -> None:
        self._acked_upto = int(idx)

    def highlight_current(self, idx: int) -> None:
        self._current_idx = int(idx)
