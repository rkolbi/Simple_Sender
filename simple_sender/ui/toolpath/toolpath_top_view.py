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
"""Top view toolpath panel."""

import math
import threading
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Iterable, Sequence

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.gcode_parser import parse_gcode_lines
from simple_sender.ui.widgets_common import _resolve_widget_bg
from simple_sender.utils.constants import (
    TOOLPATH_CANVAS_MARGIN,
    TOOLPATH_TOP_VIEW_PROGRESSIVE_CHUNK_SIZE,
    TOOLPATH_TOP_VIEW_PROGRESSIVE_RENDER_THRESHOLD,
    TOOLPATH_TOP_VIEW_RENDER_SEGMENT_LIMIT,
    TOOLPATH_GRID_MAX_POINTS,
    TOOLPATH_GRID_POINT_RADIUS,
    TOOLPATH_ORIGIN_CROSS_SIZE,
    TOOLPATH_OVERLAY_TEXT_MARGIN,
    VIEW_3D_ARC_STEP_DEFAULT,
    VIEW_3D_POSITION_MARKER_RADIUS,
)

_TOOLPATH_SEGMENT_COLORS = {
    "rapid": "#8a8a8a",
    "feed": "#2c6dd2",
    "arc": "#2aa876",
}
logger = logging.getLogger(__name__)
_logged_suppressed: set[tuple[str, str]] = set()


def _log_suppressed(context: str, exc: BaseException) -> None:
    key = (context, type(exc).__name__)
    if key in _logged_suppressed:
        return
    _logged_suppressed.add(key)
    logger.debug("%s: %s", context, exc, exc_info=exc)


class TopViewPanel(ttk.Frame):
    def __init__(self, parent: ttk.Frame) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=_resolve_widget_bg(self), highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self._schedule_render())
        self.segments: list[tuple[float, float, float, float, float, float, str]] = []
        self.bounds: tuple[float, float, float, float, float, float] | None = None
        self.position: tuple[float, float, float] | None = None
        self._job_name = ""
        self._visible = True
        self._render_pending = False
        self._parse_token = 0
        self._arc_step_rad = VIEW_3D_ARC_STEP_DEFAULT
        self._colors = _TOOLPATH_SEGMENT_COLORS
        self._last_lines_hash: str | None = None
        self._render_params: dict[str, float] | None = None
        self._position_item: Any | None = None
        self._overlay_grid: ProbeGrid | None = None
        self._status_message: str | None = None
        self._render_segment_limit = TOOLPATH_TOP_VIEW_RENDER_SEGMENT_LIMIT
        self._scene_revision = 0
        self._last_render_signature: tuple[int, int, int] | None = None
        self._progressive_render_after_id: str | int | None = None
        self._progressive_render_token = 0
        self._progressive_overlay_item: Any | None = None

    def _invalidate_scene(self) -> None:
        self._cancel_progressive_render()
        self._scene_revision += 1

    def set_lines(
        self,
        lines: Iterable[str] | None,
        *,
        max_segments: int | None = None,
        arc_step_rad: float | None = None,
    ) -> None:
        self._parse_token += 1
        token = self._parse_token
        self._last_lines_hash = None
        if arc_step_rad is not None:
            try:
                self._arc_step_rad = max(1e-6, float(arc_step_rad))
            except (TypeError, ValueError) as exc:
                _log_suppressed("Failed applying custom arc-step value for Top View parse", exc)
        if not lines:
            self.segments = []
            self.bounds = None
            self._status_message = None
            self._invalidate_scene()
            self._schedule_render()
            return
        self._status_message = "Generating top view..."
        self._invalidate_scene()
        self._schedule_render()

        def worker(parse_lines=lines, parse_token=token, max_segs=max_segments) -> None:
            def keep_running() -> bool:
                return bool(parse_token == getattr(self, "_parse_token", None))

            result = parse_gcode_lines(
                parse_lines,
                self._arc_step_rad,
                keep_running=keep_running,
                max_segments=max_segs,
                include_moves=False,
            )
            if result is None:
                return
            def schedule_apply(res=result, tok=parse_token) -> None:
                self._apply_parse_result(tok, res)

            self.after(0, schedule_apply)

        threading.Thread(target=worker, daemon=True).start()

    def apply_parsed_gcode(
        self,
        segments: Sequence[tuple[float, float, float, float, float, float, str]] | None,
        bounds: tuple[float, float, float, float, float, float] | None,
        *,
        lines_hash: str | None = None,
    ) -> None:
        segment_list = self._normalize_segments(segments)
        if lines_hash is not None and lines_hash == self._last_lines_hash:
            self.segments = segment_list
            self.bounds = bounds
            self._status_message = None
            self._invalidate_scene()
            self._schedule_render()
            return
        self._parse_token += 1
        self._last_lines_hash = lines_hash
        self.segments = segment_list
        self.bounds = bounds
        self._status_message = None
        self._invalidate_scene()
        self._schedule_render()

    @staticmethod
    def _normalize_segments(
        segments: Sequence[tuple[float, float, float, float, float, float, str]] | None,
    ) -> list[tuple[float, float, float, float, float, float, str]]:
        if not segments:
            return []
        if isinstance(segments, list):
            return segments
        return list(segments)

    def _apply_parse_result(self, token: int, result: Any) -> None:
        if token != self._parse_token or result is None:
            return
        self.segments = result.segments
        self.bounds = result.bounds
        self._status_message = None
        self._invalidate_scene()
        self._schedule_render()

    def clear(self) -> None:
        self._parse_token += 1
        self.segments = []
        self.bounds = None
        self._job_name = ""
        self._last_lines_hash = None
        self.position = None
        self._overlay_grid = None
        self._status_message = None
        self._invalidate_scene()
        self._schedule_render()

    def set_autolevel_grid(self, grid: ProbeGrid | None) -> None:
        self._overlay_grid = grid
        self._invalidate_scene()
        if self._visible:
            self._schedule_render()

    def set_job_name(self, name: str | None) -> None:
        self._job_name = str(name) if name else ""
        self._invalidate_scene()
        self._schedule_render()

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if self._visible == visible:
            return
        self._visible = visible
        if self._visible:
            self._schedule_render()

    def set_position(self, x: float, y: float, z: float) -> None:
        self.position = (x, y, z)
        if self._visible and self.segments:
            if self._render_params and not self._render_pending:
                self._update_position_marker()
            else:
                self._schedule_render()

    def _segments_bounds(
        self, segments: Sequence[tuple[float, float, float, float, float, float, str]]
    ) -> tuple[float, float, float, float, float, float] | None:
        if not segments:
            return None
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        for x1, y1, _, x2, y2, _, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
        return minx, maxx, miny, maxy, 0.0, 0.0

    def _schedule_render(self) -> None:
        if not self._visible or self._render_pending:
            return
        self._render_pending = True
        self.after_idle(self._render)

    def _cancel_progressive_render(self) -> None:
        after_id = self._progressive_render_after_id
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception as exc:
                _log_suppressed("Failed canceling pending progressive Top View render callback", exc)
        self._progressive_render_after_id = None
        self._progressive_render_token += 1

    def _draw_top_view_overlay(
        self,
        *,
        total_segments: int,
        drawn_segments: int,
        progressive: bool,
    ) -> None:
        if progressive:
            segment_text = f"Segments: {drawn_segments:,}/{total_segments:,} (drawing...)"
        elif drawn_segments == total_segments:
            segment_text = f"Segments: {total_segments:,}"
        else:
            segment_text = f"Segments: {drawn_segments:,}/{total_segments:,}"
        overlay = [segment_text, "View: Top"]
        if self._overlay_grid:
            overlay.insert(
                0,
                f"Auto-level: {len(self._overlay_grid.xs)}x{len(self._overlay_grid.ys)} "
                f"({self._overlay_grid.point_count()} pts)",
            )
        if self._job_name:
            overlay.insert(0, f"Job: {self._job_name}")
        overlay_text = "\n".join(overlay)
        if self._progressive_overlay_item is None:
            self._progressive_overlay_item = self.canvas.create_text(
                TOOLPATH_OVERLAY_TEXT_MARGIN,
                TOOLPATH_OVERLAY_TEXT_MARGIN,
                text=overlay_text,
                fill="#ffffff",
                anchor="nw",
                justify="left",
            )
            return
        try:
            self.canvas.itemconfigure(self._progressive_overlay_item, text=overlay_text)
        except tk.TclError as exc:
            _log_suppressed("Failed updating Top View overlay text item", exc)
            self._progressive_overlay_item = None

    def _render_progressive_chunk(self, token: int, state: dict[str, Any]) -> None:
        if token != self._progressive_render_token:
            return
        if not self.winfo_exists() or not self._visible:
            self._progressive_render_after_id = None
            return
        self._progressive_render_after_id = None
        render_segments = state["render_segments"]
        total_segments = int(state["total_segments"])
        stride = int(state["stride"])
        to_canvas = state["to_canvas"]
        next_idx = int(state["next_idx"])
        chunk_size = int(state["chunk_size"])
        cur_color = state["cur_color"]
        cur_pts = state["cur_pts"]
        last_end = state["last_end"]
        drawn_segments = int(state["drawn_segments"])
        eps = 1e-6
        runs: dict[str, list[list[float]]] = {}

        def flush_run() -> None:
            nonlocal cur_color, cur_pts, last_end
            if cur_color and len(cur_pts) >= 4:
                runs.setdefault(cur_color, []).append(cur_pts)
            cur_color = None
            cur_pts = []
            last_end = None

        processed = 0
        while next_idx < total_segments and processed < chunk_size:
            x1, y1, _, x2, y2, _, color = render_segments[next_idx]
            px1, py1 = to_canvas(x1, y1)
            px2, py2 = to_canvas(x2, y2)
            continuous = (
                cur_color == color
                and last_end is not None
                and abs(px1 - last_end[0]) <= eps
                and abs(py1 - last_end[1]) <= eps
            )
            if not continuous:
                flush_run()
                cur_color = color
                cur_pts = [px1, py1, px2, py2]
            else:
                cur_pts.extend([px2, py2])
            last_end = (px2, py2)
            next_idx += stride
            drawn_segments += 1
            processed += 1

        for color, polylines in runs.items():
            color_hex = self._colors.get(color, "#2c6dd2")
            for pts in polylines:
                self.canvas.create_line(*pts, fill=color_hex)

        done = next_idx >= total_segments
        if done:
            if cur_color and len(cur_pts) >= 4:
                color_hex = self._colors.get(cur_color, "#2c6dd2")
                self.canvas.create_line(*cur_pts, fill=color_hex)
            self._draw_top_view_overlay(
                total_segments=total_segments,
                drawn_segments=drawn_segments,
                progressive=False,
            )
            self._update_position_marker()
            return

        state["next_idx"] = next_idx
        state["cur_color"] = cur_color
        state["cur_pts"] = cur_pts
        state["last_end"] = last_end
        state["drawn_segments"] = drawn_segments
        self._draw_top_view_overlay(
            total_segments=total_segments,
            drawn_segments=drawn_segments,
            progressive=True,
        )
        self._update_position_marker()
        self._progressive_render_after_id = self.after(
            1,
            lambda tok=token, st=state: self._render_progressive_chunk(tok, st),
        )

    def _render(self) -> None:
        self._render_pending = False
        if not self.winfo_exists():
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        signature = (w, h, self._scene_revision)
        if signature == self._last_render_signature:
            self._update_position_marker()
            return
        self._cancel_progressive_render()
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
        self._progressive_overlay_item = None
        self._last_render_signature = signature
        if not self.segments:
            msg = self._status_message or "No G-code loaded"
            if self._job_name:
                msg = f"{self._job_name}\n{msg}"
            self.canvas.create_text(w / 2, h / 2, text=msg, fill="#666666", justify="center")
            return
        bounds = self.bounds or self._segments_bounds(self.segments)
        if not bounds:
            return
        minx, maxx, miny, maxy, _, _ = bounds
        dx = max(maxx - minx, 1e-6)
        dy = max(maxy - miny, 1e-6)
        margin = TOOLPATH_CANVAS_MARGIN
        scale_x = max(w - margin * 2, 1) / dx
        scale_y = max(h - margin * 2, 1) / dy
        scale = min(scale_x, scale_y)
        offset_x = (w - dx * scale) / 2
        offset_y = (h - dy * scale) / 2

        def to_canvas(x: float, y: float) -> tuple[float, float]:
            cx = (x - minx) * scale + offset_x
            cy = h - ((y - miny) * scale + offset_y)
            return cx, cy

        self._render_params = {
            "minx": minx,
            "miny": miny,
            "scale": scale,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "height": h,
        }

        render_segments = self.segments
        total_segments = len(render_segments)
        stride = 1
        if self._render_segment_limit > 0 and total_segments > self._render_segment_limit:
            stride = max(2, math.ceil(total_segments / self._render_segment_limit))

        x0, y0 = to_canvas(minx, miny)
        x1, y1 = to_canvas(maxx, maxy)
        self.canvas.create_rectangle(
            min(x0, x1),
            min(y0, y1),
            max(x0, x1),
            max(y0, y1),
            outline="#ffffff",
            width=1,
        )

        if self._overlay_grid:
            grid = self._overlay_grid
            gx0, gy0 = to_canvas(grid.bounds.minx, grid.bounds.miny)
            gx1, gy1 = to_canvas(grid.bounds.maxx, grid.bounds.maxy)
            self.canvas.create_rectangle(
                min(gx0, gx1),
                min(gy0, gy1),
                max(gx0, gx1),
                max(gy0, gy1),
                outline="#ffb347",
                width=2,
                dash=(4, 3),
            )
            points = grid.points
            if points:
                max_points = TOOLPATH_GRID_MAX_POINTS
                step = max(1, len(points) // max_points) if len(points) > max_points else 1
                r = TOOLPATH_GRID_POINT_RADIUS
                for idx in range(0, len(points), step):
                    px, py = points[idx]
                    cx, cy = to_canvas(px, py)
                    self.canvas.create_oval(
                        cx - r,
                        cy - r,
                        cx + r,
                        cy + r,
                        fill="#ffb347",
                        outline="",
                    )

        if minx <= 0 <= maxx and miny <= 0 <= maxy:
            ox, oy = to_canvas(0.0, 0.0)
            cross = TOOLPATH_ORIGIN_CROSS_SIZE
            self.canvas.create_line(ox - cross, oy, ox + cross, oy, fill="#ffffff")
            self.canvas.create_line(ox, oy - cross, ox, oy + cross, fill="#ffffff")

        drawn_segments = len(range(0, total_segments, stride))
        progressive_threshold = max(1, int(TOOLPATH_TOP_VIEW_PROGRESSIVE_RENDER_THRESHOLD))
        if drawn_segments >= progressive_threshold:
            chunk_size = max(100, int(TOOLPATH_TOP_VIEW_PROGRESSIVE_CHUNK_SIZE))
            self._draw_top_view_overlay(
                total_segments=total_segments,
                drawn_segments=0,
                progressive=True,
            )
            token = self._progressive_render_token
            state: dict[str, Any] = {
                "render_segments": render_segments,
                "total_segments": total_segments,
                "stride": stride,
                "next_idx": 0,
                "chunk_size": chunk_size,
                "cur_color": None,
                "cur_pts": [],
                "last_end": None,
                "drawn_segments": 0,
                "to_canvas": to_canvas,
            }
            self._render_progressive_chunk(token, state)
            return

        runs: dict[str, list[list[float]]] = {}
        cur_color = None
        cur_pts: list[float] = []
        last_end = None

        def flush_run() -> None:
            nonlocal cur_color, cur_pts, last_end
            if cur_color and len(cur_pts) >= 4:
                runs.setdefault(cur_color, []).append(cur_pts)
            cur_color = None
            cur_pts = []
            last_end = None

        eps = 1e-6
        for idx in range(0, total_segments, stride):
            x1, y1, _, x2, y2, _, color = render_segments[idx]
            px1, py1 = to_canvas(x1, y1)
            px2, py2 = to_canvas(x2, y2)
            continuous = (
                cur_color == color
                and last_end is not None
                and abs(px1 - last_end[0]) <= eps
                and abs(py1 - last_end[1]) <= eps
            )
            if not continuous:
                flush_run()
                cur_color = color
                cur_pts = [px1, py1, px2, py2]
            else:
                cur_pts.extend([px2, py2])
            last_end = (px2, py2)
        flush_run()

        for color, polylines in runs.items():
            color_hex = self._colors.get(color, "#2c6dd2")
            for pts in polylines:
                self.canvas.create_line(*pts, fill=color_hex)

        self._draw_top_view_overlay(
            total_segments=total_segments,
            drawn_segments=drawn_segments,
            progressive=False,
        )
        self._update_position_marker()

    def _update_position_marker(self) -> None:
        if not self._render_params:
            return
        if not self.position:
            if self._position_item is not None:
                try:
                    self.canvas.delete(self._position_item)
                except tk.TclError as exc:
                    _log_suppressed("Failed deleting stale Top View position marker", exc)
                self._position_item = None
            return
        params = self._render_params
        px, py, _ = self.position
        cx = (px - params["minx"]) * params["scale"] + params["offset_x"]
        cy = params["height"] - ((py - params["miny"]) * params["scale"] + params["offset_y"])
        r = VIEW_3D_POSITION_MARKER_RADIUS
        if self._position_item is None:
            self._position_item = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#d64545", outline=""
            )
        else:
            self.canvas.coords(self._position_item, cx - r, cy - r, cx + r, cy + r)
        try:
            self.canvas.tag_raise(self._position_item)
        except tk.TclError as exc:
            _log_suppressed("Failed raising Top View position marker above toolpath lines", exc)


