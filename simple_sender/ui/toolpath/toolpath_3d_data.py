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
"""3D toolpath parsing and data helpers."""

import threading
import time
from typing import Any, Callable, cast

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.gcode_parser import parse_gcode_lines
from . import toolpath_3d_projection
from simple_sender.utils.constants import (
    TOOLPATH_STREAMING_RENDER_INTERVAL_MAX,
    TOOLPATH_STREAMING_RENDER_INTERVAL_MIN,
    VIEW_3D_ARC_STEP_FAST_THRESHOLD,
)
from simple_sender.utils.hashing import hash_lines as _hash_lines


class Toolpath3DDataMixin:
    _perf_callback: Callable[[str, float], None] | None
    _perf_threshold: float
    _cached_projection_state: Any
    _cached_projection: Any
    _cached_projection_metrics: Any
    _draw_percent_default: int
    _draw_percent: int
    _draw_percent_text: Any
    _full_parse_skipped: bool
    _last_gcode_lines: list[str] | None
    _full_parse_limit: int
    _streaming_mode: bool
    _deferred_full_parse: bool
    _last_lines_hash: str | None
    _last_segments: list[tuple[float, float, float, float, float, float, str]] | None
    _last_bounds: tuple[float, float, float, float, float, float] | None
    _pending_parsed: Any
    _pending_lines: list[str] | None
    segments: list[tuple[float, float, float, float, float, float, str]]
    bounds: tuple[float, float, float, float, float, float] | None
    _parse_token: int
    _arc_step_default: float
    _arc_step_fast: float
    _arc_step_large: float
    _arc_step_rad: float
    _arc_step_override_rad: float | None
    _max_draw_segments: int | None
    _interactive_max_draw_segments: int | None
    _preview_target: int
    _lightweight_preview_target: int
    _lightweight_mode: bool
    _streaming_prev_render_interval: float | None
    _streaming_render_interval: float
    _render_interval: float
    _visible: bool
    enabled: bool
    show_rapid: Any
    show_feed: Any
    show_arc: Any
    _job_name: str
    _overlay_grid: ProbeGrid | None
    _project: Callable[[float, float, float], tuple[float, float]]
    _schedule_render: Callable[[], None]

    def _report_perf(self, label: str, duration: float):
        if not self._perf_callback:
            return
        if duration < self._perf_threshold:
            return
        try:
            self._perf_callback(label, duration)
        except Exception:
            pass

    def _invalidate_render_cache(self):
        self._cached_projection_state = None
        self._cached_projection = None
        self._cached_projection_metrics = None

    def _clamp_draw_percent(self, value) -> int:
        try:
            percent = int(round(float(value)))
        except Exception:
            percent = self._draw_percent_default
        return max(0, min(100, percent))

    def _apply_draw_percent(self, percent: int, update_scale: bool):
        if percent == self._draw_percent:
            return
        self._draw_percent = percent
        self._draw_percent_text.set(f"{percent}%")
        if update_scale:
            scale = getattr(self, "draw_percent_scale", None)
            if scale is not None:
                try:
                    scale.set(percent)
                except Exception:
                    pass
        self._invalidate_render_cache()
        self._schedule_render()
        if (
            percent >= 100
            and self._full_parse_skipped
            and self._last_gcode_lines
            and len(self._last_gcode_lines) > self._full_parse_limit
        ):
            if self._streaming_mode:
                self._deferred_full_parse = True
            else:
                self.set_gcode_async(self._last_gcode_lines)

    def _draw_target(self, total_segments: int, max_draw: int | None) -> int:
        return toolpath_3d_projection.draw_target(
            self._draw_percent,
            total_segments,
            max_draw,
        )

    def _sample_segments(self, segments, target: int):
        return toolpath_3d_projection.sample_segments(segments, target)

    def _build_projection_cache(self, filters: tuple[bool, bool, bool], max_draw: int | None):
        start = time.perf_counter()
        try:
            return toolpath_3d_projection.build_projection_cache(
                segments=self.segments,
                draw_percent=self._draw_percent,
                max_draw=max_draw,
                filters=filters,
                project=self._project,
            )
        finally:
            self._report_perf("build_projection", time.perf_counter() - start)

    def set_display_options(
        self,
        rapid: bool | None = None,
        feed: bool | None = None,
        arc: bool | None = None,
    ):
        changed = False
        if rapid is not None:
            self.show_rapid.set(bool(rapid))
            changed = True
        if feed is not None:
            self.show_feed.set(bool(feed))
            changed = True
        if arc is not None:
            self.show_arc.set(bool(arc))
            changed = True
        if changed:
            self._schedule_render()
            self._invalidate_render_cache()

    def get_display_options(self) -> tuple[bool, bool, bool]:
        return (
            bool(self.show_rapid.get()),
            bool(self.show_feed.get()),
            bool(self.show_arc.get()),
        )

    def set_gcode(self, lines: list[str]):
        segs, bnds = self._parse_gcode(lines)
        if segs is not None:
            self.segments, self.bounds = segs, bnds
            self._invalidate_render_cache()
        self._schedule_render()

    def select_arc_step_rad(self, line_count: int) -> float:
        if line_count > self._full_parse_limit:
            base_step = self._arc_step_large
        elif line_count > VIEW_3D_ARC_STEP_FAST_THRESHOLD:
            base_step = self._arc_step_fast
        else:
            base_step = self._arc_step_default
        if self._arc_step_override_rad is not None:
            return float(self._arc_step_override_rad)
        return float(base_step)

    def set_gcode_async(self, lines: list[str], *, lines_hash: str | None = None):
        self._parse_token += 1
        token = self._parse_token
        self._last_gcode_lines = lines
        lines_hash = lines_hash if lines_hash is not None else _hash_lines(lines)
        if (
            lines_hash
            and (lines_hash == self._last_lines_hash)
            and self._last_segments is not None
            and not self._full_parse_skipped
        ):
            self.segments = self._last_segments
            self.bounds = self._last_bounds
            self._schedule_render()
            return
        line_count = len(lines)
        self._arc_step_rad = self.select_arc_step_rad(line_count)
        if not self.enabled:
            self._pending_parsed = None
            self._pending_lines = lines
            return
        self._pending_lines = None
        if not lines:
            self._pending_parsed = None
            self.segments = []
            self.bounds = None
            self._full_parse_skipped = False
            self._schedule_render()
            return
        preview_target = (
            self._lightweight_preview_target
            if (self._lightweight_mode or self._streaming_mode)
            else self._preview_target
        )
        quick_lines = lines
        if len(lines) > preview_target:
            step = max(2, len(lines) // preview_target)
            quick_lines = lines[::step]
        res = self._parse_gcode(quick_lines, token)
        if res[0] is None:
            return
        self.segments, self.bounds = res
        if quick_lines is lines:
            self._cache_parse_results(lines_hash, self.segments, self.bounds)
        self._schedule_render()
        if len(lines) > self._full_parse_limit:
            allow_full_parse = self._draw_percent >= 100 or self._max_draw_segments is None
            if self._streaming_mode:
                allow_full_parse = False
            if not allow_full_parse:
                self._full_parse_skipped = True
                return
        if self._streaming_mode:
            self._full_parse_skipped = quick_lines is not lines
            return
        self._full_parse_skipped = False

        def worker():
            segs, bnds = self._parse_gcode(lines, token)
            if segs is None:
                return
            widget = cast(Any, self)
            if not widget.winfo_exists():
                return
            root = widget.winfo_toplevel()
            if getattr(root, "_closing", False):
                return
            cast(Any, self).after(0, lambda: self._apply_full_parse(token, segs, bnds, lines_hash))

        threading.Thread(target=worker, daemon=True).start()

    def apply_parsed_gcode(
        self,
        lines: list[str],
        segments,
        bounds,
        *,
        lines_hash: str | None = None,
    ):
        self._parse_token += 1
        self._last_gcode_lines = lines
        line_count = len(lines)
        self._arc_step_rad = self.select_arc_step_rad(line_count)
        lines_hash = lines_hash if lines_hash is not None else _hash_lines(lines)
        segments = segments or []
        self._cache_parse_results(lines_hash, segments, bounds)
        self._full_parse_skipped = False
        self._pending_lines = None
        if not self.enabled:
            self._pending_parsed = (segments, bounds, lines_hash)
            self.segments = []
            self.bounds = None
            return
        self._pending_parsed = None
        self.segments = segments
        self.bounds = bounds
        self._invalidate_render_cache()
        self._schedule_render()

    def _cache_parse_results(self, lines_hash: str | None, segments, bounds):
        if not lines_hash:
            return
        self._last_lines_hash = lines_hash
        self._last_segments = segments
        self._last_bounds = bounds

    def set_lightweight_mode(self, lightweight: bool):
        new_mode = bool(lightweight)
        if self._lightweight_mode == new_mode:
            return
        self._lightweight_mode = new_mode
        self._schedule_render()

    def set_job_name(self, name: str | None):
        self._job_name = str(name) if name else ""
        self._schedule_render()

    def set_autolevel_grid(self, grid: ProbeGrid | None):
        self._overlay_grid = grid
        if self._visible:
            self._schedule_render()

    def _apply_full_parse(self, token, segments, bounds, parse_hash: str | None = None):
        widget = cast(Any, self)
        if not widget.winfo_exists():
            return
        root = widget.winfo_toplevel()
        if getattr(root, "_closing", False):
            return
        if token != self._parse_token:
            return
        if not self.enabled:
            self._pending_lines = None
            return
        self._full_parse_skipped = False
        self._cache_parse_results(parse_hash, segments, bounds)
        self.segments = segments
        self.bounds = bounds
        self._invalidate_render_cache()
        self._schedule_render()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.segments = []
            self.bounds = None
            self._schedule_render()
            return
        if self._pending_parsed is not None:
            segments, bounds, lines_hash = self._pending_parsed
            self._pending_parsed = None
            self._cache_parse_results(lines_hash, segments, bounds)
            self.segments = segments
            self.bounds = bounds
            self._full_parse_skipped = False
            self._invalidate_render_cache()
            self._schedule_render()
            return
        if self._pending_lines is not None:
            pending = self._pending_lines
            self._pending_lines = None
            self.set_gcode_async(pending)

    def set_draw_limits(self, full_limit: int | None = None, interactive_limit: int | None = None):
        if full_limit is not None:
            if full_limit <= 0:
                self._max_draw_segments = None
            else:
                self._max_draw_segments = int(full_limit)
        if interactive_limit is not None:
            if interactive_limit <= 0:
                self._interactive_max_draw_segments = None
            else:
                self._interactive_max_draw_segments = int(interactive_limit)
        self._invalidate_render_cache()
        self._schedule_render()

    def set_draw_percent(self, percent):
        percent = self._clamp_draw_percent(percent)
        self._apply_draw_percent(percent, update_scale=True)

    def get_draw_percent(self) -> int:
        return int(self._draw_percent)

    def set_arc_detail_override(self, step_rad: float | None):
        if step_rad is None or step_rad <= 0:
            self._arc_step_override_rad = None
        else:
            self._arc_step_override_rad = float(step_rad)
        self._schedule_render()

    def set_streaming_render_interval(self, interval: float):
        try:
            value = float(interval)
        except Exception:
            return
        value = max(
            TOOLPATH_STREAMING_RENDER_INTERVAL_MIN,
            min(TOOLPATH_STREAMING_RENDER_INTERVAL_MAX, value),
        )
        self._streaming_render_interval = value
        if self._streaming_mode:
            base_interval = (
                self._streaming_prev_render_interval
                if self._streaming_prev_render_interval is not None
                else self._render_interval
            )
            self._render_interval = max(base_interval, self._streaming_render_interval)
            self._schedule_render()

    def set_streaming_mode(self, streaming: bool):
        streaming = bool(streaming)
        if self._streaming_mode == streaming:
            return
        self._streaming_mode = streaming
        if streaming:
            self._streaming_prev_render_interval = self._render_interval
            self._render_interval = max(self._render_interval, self._streaming_render_interval)
        else:
            if self._streaming_prev_render_interval is not None:
                self._render_interval = self._streaming_prev_render_interval
            if self._deferred_full_parse and self._last_gcode_lines:
                self._deferred_full_parse = False
                self.set_gcode_async(self._last_gcode_lines)
        self._schedule_render()

    def _on_draw_percent_slider(self, value):
        percent = self._clamp_draw_percent(value)
        self._apply_draw_percent(percent, update_scale=False)

    def _parse_gcode(self, lines: list[str], token: int | None = None):
        start = time.perf_counter()
        try:
            def keep_running() -> bool:
                return token is None or token == self._parse_token

            result = parse_gcode_lines(lines, self._arc_step_rad, keep_running=keep_running)
            if result is None:
                return None, None
            return result.segments, result.bounds
        finally:
            self._report_perf("parse_gcode", time.perf_counter() - start)
