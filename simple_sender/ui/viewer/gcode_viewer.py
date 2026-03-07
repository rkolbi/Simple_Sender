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

"""Bounded live G-code viewer (Past/Current/Next window).

Lean sender mode keeps the G-code tab lightweight by rendering only a fixed
window sourced from streaming worker queues. Legacy full-file text rendering
entry points are intentionally no-ops.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...utils.constants import (
    GCODE_LIVE_WINDOW_NEXT_LINES,
    GCODE_LIVE_WINDOW_PAST_LINES,
    COLOR_GCODE_CURRENT,
    COLOR_GCODE_TEXT,
    COLOR_GCODE_BG,
)


def reset_gcode_view_for_run(app) -> None:
    if not hasattr(app, "gview"):
        return
    app._clear_pending_ui_updates()
    app.gview.clear_highlights()
    app._last_sent_index = -1
    app._last_acked_index = -1
    app._last_error_index = -1


class GcodeViewer(ttk.Frame):
    """Text widget for bounded live Past/Current/Next G-code display."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent)

        self.text = tk.Text(self, wrap="none", height=18, undo=False)
        self.text.configure(
            background=COLOR_GCODE_BG,
            foreground=COLOR_GCODE_TEXT,
            insertbackground=COLOR_GCODE_TEXT,
        )

        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.text.tag_configure("current", background=COLOR_GCODE_CURRENT, foreground=COLOR_GCODE_TEXT)

        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self._live_mode = True
        self._live_current_row: int | None = None

    # ------------------------------------------------------------------
    # Lean live-window API
    # ------------------------------------------------------------------
    def set_live_window(
        self,
        past_lines: list[tuple[int, str]],
        current_line: tuple[int, str] | None,
        next_lines: list[tuple[int, str]],
        *,
        next_buffered_count: int = 0,
        highlight_current: bool = True,
    ) -> None:
        """Render a bounded live Past/Current/Next G-code window."""
        self._live_mode = True
        self._live_current_row = None
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1

        past = list(past_lines[-int(GCODE_LIVE_WINDOW_PAST_LINES):])
        nxt = list(next_lines[: int(GCODE_LIVE_WINDOW_NEXT_LINES)])

        lines: list[str] = ["--- Past (last 500 acked) ---"]
        for idx, raw in past:
            lines.append(self._format_live_line(idx, raw))

        lines.append("--- Current (acked) ---")
        if current_line is None:
            lines.append("<none>")
        else:
            self._live_current_row = len(lines) + 1
            lines.append(self._format_live_line(current_line[0], current_line[1]))

        lines.append("--- Next (up to 500 queued) ---")
        for idx, raw in nxt:
            lines.append(self._format_live_line(idx, raw))

        buffered = max(0, int(next_buffered_count))
        if buffered < len(nxt):
            buffered = len(nxt)
        lines.append(f"Next: {buffered} buffered")

        self.lines_count = len(lines)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "\n".join(lines) + "\n")
        self.text.tag_remove("current", "1.0", "end")
        if highlight_current and self._live_current_row is not None and current_line is not None:
            start = f"{int(self._live_current_row)}.0"
            end = f"{int(self._live_current_row) + 1}.0"
            self.text.tag_add("current", start, end)
        self.text.config(state="disabled")

    @staticmethod
    def _format_live_line(idx: int | None, line: str) -> str:
        text = str(line or "")
        if idx is None:
            return f"      ? | {text}"
        try:
            line_no = int(idx) + 1
        except Exception:
            line_no = 0
        if line_no <= 0:
            return f"      ? | {text}"
        return f"{line_no:7d} | {text}"

    def clear(self) -> None:
        self.lines_count = 0
        self._sent_upto = -1
        self._acked_upto = -1
        self._current_idx = -1
        self._live_current_row = None
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def clear_highlights(self) -> None:
        self.text.config(state="normal")
        self.text.tag_remove("current", "1.0", "end")
        self.text.config(state="disabled")
        self._current_idx = -1
        self._live_current_row = None

    def mark_sent_upto(self, idx: int) -> None:
        """Compatibility hook for status/resume code paths (state only, no render)."""
        self._sent_upto = int(idx)

    def mark_acked_upto(self, idx: int) -> None:
        """Compatibility hook for status/resume code paths (state only, no render)."""
        self._acked_upto = int(idx)

    def highlight_current(self, idx: int) -> None:
        """Compatibility hook for current-line tracking (state only, no render)."""
        self._current_idx = int(idx)
