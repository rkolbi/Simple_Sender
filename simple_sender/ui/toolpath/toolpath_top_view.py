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

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Iterable, Sequence

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.gcode_parser import parse_gcode_lines
from simple_sender.ui.widgets import _resolve_widget_bg
from simple_sender.utils.constants import (
    TOOLPATH_CANVAS_MARGIN,
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
            except Exception:
                pass
        if not lines:
            self.segments = []
            self.bounds = None
            self._status_message = None
            self._schedule_render()
            return
        self._status_message = "Generating top view..."
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
        if lines_hash is not None and lines_hash == self._last_lines_hash:
            self.segments = list(segments) if segments else []
            self.bounds = bounds
            self._status_message = None
            self._schedule_render()
            return
        self._parse_token += 1
        self._last_lines_hash = lines_hash
        self.segments = list(segments) if segments else []
        self.bounds = bounds
        self._status_message = None
        self._schedule_render()

    def _apply_parse_result(self, token: int, result: Any) -> None:
        if token != self._parse_token or result is None:
            return
        self.segments = result.segments
        self.bounds = result.bounds
        self._status_message = None
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
        self._schedule_render()

    def set_autolevel_grid(self, grid: ProbeGrid | None) -> None:
        self._overlay_grid = grid
        if self._visible:
            self._schedule_render()

    def set_job_name(self, name: str | None) -> None:
        self._job_name = str(name) if name else ""
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

    def _render(self) -> None:
        self._render_pending = False
        if not self.winfo_exists():
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
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
        for x1, y1, _, x2, y2, _, color in self.segments:
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

        overlay = [f"Segments: {len(self.segments):,}", "View: Top"]
        if self._overlay_grid:
            overlay.insert(
                0,
                f"Auto-level: {len(self._overlay_grid.xs)}x{len(self._overlay_grid.ys)} "
                f"({self._overlay_grid.point_count()} pts)",
            )
        if self._job_name:
            overlay.insert(0, f"Job: {self._job_name}")
        self.canvas.create_text(
            TOOLPATH_OVERLAY_TEXT_MARGIN,
            TOOLPATH_OVERLAY_TEXT_MARGIN,
            text="\n".join(overlay),
            fill="#ffffff",
            anchor="nw",
            justify="left",
        )

        self._update_position_marker()

    def _update_position_marker(self) -> None:
        if not self._render_params:
            return
        if not self.position:
            if self._position_item is not None:
                try:
                    self.canvas.delete(self._position_item)
                except Exception:
                    pass
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


