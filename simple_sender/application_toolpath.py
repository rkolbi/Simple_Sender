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
""" 
    Simple Sender - GRBL 1.1h CNC Controller
"""

# Standard library imports
from simple_sender.ui.app_exports import (
    apply_toolpath_arc_detail,
    apply_toolpath_draw_limits,
    apply_toolpath_performance,
    apply_toolpath_streaming_render_interval,
    clamp_arc_detail,
    clamp_toolpath_performance,
    clamp_toolpath_streaming_render_interval,
    load_3d_view,
    on_arc_detail_scale_key_release,
    on_arc_detail_scale_move,
    on_toolpath_lightweight_change,
    on_toolpath_performance_key_release,
    on_toolpath_performance_move,
    refresh_render_3d_toggle_text,
    run_toolpath_arc_detail_reparse,
    save_3d_view,
    schedule_toolpath_arc_detail_reparse,
    toggle_render_3d,
    toolpath_limit_value,
    toolpath_perf_values,
)


class ToolpathMixin:
    def _refresh_render_3d_toggle_text(self):
        refresh_render_3d_toggle_text(self)

    def _toggle_render_3d(self):
        toggle_render_3d(self)

    def _toolpath_limit_value(self, raw, fallback):
        return toolpath_limit_value(self, raw, fallback)

    def _clamp_toolpath_performance(self, value):
        return clamp_toolpath_performance(self, value)

    def _clamp_toolpath_streaming_render_interval(self, value):
        return clamp_toolpath_streaming_render_interval(self, value)

    def _apply_toolpath_streaming_render_interval(self, _event=None):
        apply_toolpath_streaming_render_interval(self, _event)

    def _toolpath_perf_values(self, perf: float):
        return toolpath_perf_values(self, perf)

    def _on_toolpath_performance_move(self, value):
        on_toolpath_performance_move(self, value)

    def _on_toolpath_performance_key_release(self, event):
        on_toolpath_performance_key_release(self, event)

    def _apply_toolpath_performance(self, _event=None):
        apply_toolpath_performance(self, _event)

    def _apply_toolpath_draw_limits(self, _event=None):
        apply_toolpath_draw_limits(self, _event)

    def _on_arc_detail_scale_move(self, value):
        on_arc_detail_scale_move(self, value)

    def _on_arc_detail_scale_key_release(self, event):
        on_arc_detail_scale_key_release(self, event)

    def _clamp_arc_detail(self, value):
        return clamp_arc_detail(self, value)

    def _apply_toolpath_arc_detail(self, _event=None):
        apply_toolpath_arc_detail(self, _event)

    def _schedule_toolpath_arc_detail_reparse(self):
        schedule_toolpath_arc_detail_reparse(self)

    def _run_toolpath_arc_detail_reparse(self):
        run_toolpath_arc_detail_reparse(self)

    def _on_toolpath_lightweight_change(self):
        on_toolpath_lightweight_change(self)

    def _save_3d_view(self):
        save_3d_view(self)

    def _load_3d_view(self, show_status: bool = True):
        load_3d_view(self, show_status)

