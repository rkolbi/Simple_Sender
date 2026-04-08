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
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1


class HeadlessGcodeView:
    """Non-widget live-window/job-view state holder used by the current runtime.

    The redesigned lower UI no longer exposes a visible G-code pane. Runtime
    code still expects ``app.gview`` for lightweight job-present checks and
    load/reset state, so this headless object keeps those semantics without a
    widget tree. ``lines_count`` is a lightweight internal estimate used by
    diagnostics and job-present checks, not a literal visible-window line total.
    """

    def __init__(self) -> None:
        self.lines_count = 0

    def set_live_window(
        self,
        past_lines: list[tuple[int, str]],
        current_line: tuple[int, str] | None,
        next_lines: list[tuple[int, str]],
        *,
        next_buffered_count: int = 0,
    ) -> None:
        filtered_past: list[tuple[int, str]] = []
        for idx, raw in past_lines:
            try:
                idx_i = int(idx)
            except Exception:
                continue
            filtered_past.append((idx_i, str(raw or "")))
        past = list(filtered_past[-int(GCODE_LIVE_WINDOW_PAST_LINES):])
        look_ahead = list(next_lines[: int(GCODE_LIVE_WINDOW_LOOKAHEAD_LINES)])

        # Keep a small synthetic line estimate for diagnostics/job-present
        # checks without rebuilding a literal retained visible window.
        line_count = 2
        line_count += max(1, int(len(look_ahead)))
        line_count += 2
        line_count += int(len(past))
        self.lines_count = int(line_count)

    def clear(self) -> None:
        self.lines_count = 0
