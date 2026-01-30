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
"""Toolpath panel coordinator for top and 3D views."""

import math
from tkinter import ttk
from typing import Any, Iterable, Optional

from simple_sender.autolevel.grid import ProbeGrid
from .toolpath_3d import Toolpath3D
from .toolpath_top_view import TopViewPanel
from simple_sender.utils.constants import (
    VIEW_3D_ARC_STEP_DEFAULT,
    VIEW_3D_DRAW_PERCENT_DEFAULT,
    VIEW_3D_PERF_LOG_THRESHOLD,
)


class ToolpathPanel:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.view: Toolpath3D | None = None
        self.top_view: Optional["TopViewPanel"] = None
        self.tab: ttk.Frame | None = None
        self._streaming = False
        self._pending_gcode_lines: list[str] | None = None
        self._pending_gcode_hash: str | None = None
        self._pending_top_request: tuple[Iterable[str] | None, int | None, float | None] | None = None
        self._pending_parsed: tuple[list[str], Any, str | None] | None = None
        self._pending_top_parsed: tuple[Any, str | None] | None = None
        self._pending_top_overlay: ProbeGrid | None = None
        self._pending_view_overlay: ProbeGrid | None = None

    def build_tab(self, notebook: ttk.Notebook):
        top_tab = ttk.Frame(notebook, padding=6)
        notebook.add(top_tab, text="Top View")
        self.top_view = TopViewPanel(top_tab)
        self.top_view.pack(fill="both", expand=True)
        if self._pending_top_overlay is not None:
            self.top_view.set_autolevel_grid(self._pending_top_overlay)
            self._pending_top_overlay = None

        tab = ttk.Frame(notebook, padding=6)
        notebook.add(tab, text="3D View")
        self.tab = tab
        self.view = Toolpath3D(
            tab,
            on_save_view=self.app._save_3d_view,
            on_load_view=self.app._load_3d_view,
            perf_callback=self._toolpath_perf_logger,
        )
        self.view.pack(fill="both", expand=True)
        self._configure_view()
        self.view.set_streaming_mode(self._streaming)
        self.app._load_3d_view(show_status=False)
        if self._pending_parsed is not None and self.view:
            lines, result, lines_hash = self._pending_parsed
            self._pending_parsed = None
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.apply_parsed_gcode(lines, result.segments, result.bounds, lines_hash=lines_hash)
        if self._pending_gcode_lines is not None and self.view and getattr(self.view, "_visible", True):
            lines = self._pending_gcode_lines
            lines_hash = self._pending_gcode_hash
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.set_gcode_async(lines, lines_hash=lines_hash)
        if self._pending_view_overlay is not None and self.view:
            self.view.set_autolevel_grid(self._pending_view_overlay)
            self._pending_view_overlay = None
        if self._pending_top_parsed is not None and self.top_view:
            result, lines_hash = self._pending_top_parsed
            self._pending_top_parsed = None
            self._pending_top_request = None
            self.top_view.apply_parsed_gcode(result.segments, result.bounds, lines_hash=lines_hash)
        if self._pending_top_request is not None and self.top_view and getattr(self.top_view, "_visible", True):
            pending_lines, max_segments, arc_step_rad = self._pending_top_request
            self._pending_top_request = None
            self.top_view.set_lines(pending_lines, max_segments=max_segments, arc_step_rad=arc_step_rad)

    def set_autolevel_overlay(self, grid: ProbeGrid | None):
        if self.top_view:
            self.top_view.set_autolevel_grid(grid)
        else:
            self._pending_top_overlay = grid
        if self.view:
            self.view.set_autolevel_grid(grid)
        else:
            self._pending_view_overlay = grid

    def _configure_view(self):
        if not self.view:
            return
        self.view.set_display_options(
            rapid=bool(self.app.settings.get("toolpath_show_rapid", False)),
            feed=bool(self.app.settings.get("toolpath_show_feed", True)),
            arc=bool(self.app.settings.get("toolpath_show_arc", False)),
        )
        self.view.set_performance_controls(
            self.app.toolpath_performance,
            self.app._toolpath_performance_value,
            self.app._on_toolpath_performance_move,
            self.app._apply_toolpath_performance,
            self.app._on_toolpath_performance_key_release,
        )
        self.view.set_enabled(bool(self.app.render3d_enabled.get()))
        self.view.set_lightweight_mode(bool(self.app.toolpath_lightweight.get()))
        self.view.set_draw_limits(
            self.app._toolpath_limit_value(self.app.toolpath_full_limit.get(), self.app._toolpath_full_limit_default),
            self.app._toolpath_limit_value(self.app.toolpath_interactive_limit.get(), self.app._toolpath_interactive_limit_default),
        )
        draw_percent = getattr(self.app, "_toolpath_draw_percent", None)
        if draw_percent is None:
            draw_percent = self.app.settings.get("toolpath_draw_percent", VIEW_3D_DRAW_PERCENT_DEFAULT)
        self.view.set_draw_percent(draw_percent)
        self.view.set_streaming_render_interval(self.app.toolpath_streaming_render_interval.get())
        self.view.set_arc_detail_override(math.radians(self.app.toolpath_arc_detail.get()))

    def _toolpath_perf_logger(self, label: str, duration: float):
        if duration < VIEW_3D_PERF_LOG_THRESHOLD:
            return
        try:
            self.app.ui_q.put(("log", f"[toolpath] {label} took {duration:.2f}s"))
        except Exception:
            pass

    def get_arc_step_rad(self, line_count: int) -> float:
        if self.view:
            return float(self.view.select_arc_step_rad(line_count))
        return float(VIEW_3D_ARC_STEP_DEFAULT)

    def apply_parse_result(self, lines: list[str], result, lines_hash: str | None = None):
        if result is None:
            return
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self._pending_top_request = None
        if self.view:
            self._pending_parsed = None
            self.view.apply_parsed_gcode(lines, result.segments, result.bounds, lines_hash=lines_hash)
        else:
            self._pending_parsed = (lines, result, lines_hash)
        if self.top_view:
            self._pending_top_parsed = None
            self.top_view.apply_parsed_gcode(result.segments, result.bounds, lines_hash=lines_hash)
        else:
            self._pending_top_parsed = (result, lines_hash)

    def set_gcode_lines(self, lines: list[str], lines_hash: str | None = None):
        self._pending_parsed = None
        if not self.view or not getattr(self.view, "_visible", True):
            self._pending_gcode_lines = lines
            self._pending_gcode_hash = lines_hash
            return
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_top_view_lines(
        self,
        lines: Iterable[str] | None,
        *,
        max_segments: int | None = None,
        arc_step_rad: float | None = None,
    ):
        self._pending_top_parsed = None
        if not self.top_view or not getattr(self.top_view, "_visible", True):
            self._pending_top_request = (lines if lines else [], max_segments, arc_step_rad)
            return
        self._pending_top_request = None
        self.top_view.set_lines(lines, max_segments=max_segments, arc_step_rad=arc_step_rad)

    def clear(self):
        self._pending_parsed = None
        self._pending_top_parsed = None
        self._pending_gcode_lines = None
        self._pending_gcode_hash = None
        self._pending_top_request = None
        if self.view:
            self.view.set_gcode_async([])
            self.view.set_job_name("")
        if self.top_view:
            self.top_view.clear()

    def set_job_name(self, name: str):
        if self.view:
            self.view.set_job_name(name)
        if self.top_view:
            self.top_view.set_job_name(name)

    def set_visible(self, visible: bool):
        if self.view:
            self.view.set_visible(visible)
        # Keep the top view hidden only when its tab is not selected.
        if visible and self._pending_gcode_lines is not None and self.view:
            lines = self._pending_gcode_lines
            lines_hash = self._pending_gcode_hash
            self._pending_gcode_lines = None
            self._pending_gcode_hash = None
            self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_top_view_visible(self, visible: bool):
        if self.top_view:
            self.top_view.set_visible(visible)
        if visible and self._pending_top_request is not None and self.top_view:
            lines, max_segments, arc_step_rad = self._pending_top_request
            self._pending_top_request = None
            self.top_view.set_lines(lines, max_segments=max_segments, arc_step_rad=arc_step_rad)

    def set_enabled(self, enabled: bool):
        if self.view:
            self.view.set_enabled(enabled)

    def set_lightweight(self, value: bool):
        if self.view:
            self.view.set_lightweight_mode(value)

    def set_draw_limits(self, full: int, interactive: int):
        if self.view:
            self.view.set_draw_limits(full, interactive)

    def set_arc_detail(self, deg: float):
        if self.view:
            self.view.set_arc_detail_override(math.radians(deg))

    def set_draw_percent(self, percent: int):
        if self.view:
            self.view.set_draw_percent(percent)

    def set_streaming_render_interval(self, interval: float):
        if self.view:
            self.view.set_streaming_render_interval(interval)

    def set_streaming(self, streaming: bool):
        self._streaming = bool(streaming)
        if self.view:
            self.view.set_streaming_mode(self._streaming)

    def reparse_lines(self, lines: list[str], lines_hash: str | None = None):
        if self.view:
            self.view.set_gcode_async(lines, lines_hash=lines_hash)

    def set_position(self, x: float, y: float, z: float):
        if self.view:
            self.view.set_position(x, y, z)
        if self.top_view:
            self.top_view.set_position(x, y, z)

    def get_view_state(self):
        if self.view:
            return self.view.get_view()
        return None

    def apply_view_state(self, state):
        if self.view and state:
            self.view.apply_view(state)

    def get_draw_percent(self):
        if self.view:
            return self.view.get_draw_percent()
        return getattr(self.app, "_toolpath_draw_percent", VIEW_3D_DRAW_PERCENT_DEFAULT)

    def get_display_options(self):
        if self.view:
            return self.view.get_display_options()
        return (False, False, False)
