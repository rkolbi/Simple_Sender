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
"""3D toolpath rendering helpers."""

import math
import time

from simple_sender.ui import toolpath_3d_render
from simple_sender.utils.constants import (
    TOOLPATH_CANVAS_MARGIN,
    VIEW_3D_POSITION_MARKER_RADIUS,
)


class Toolpath3DRenderMixin:
    def set_visible(self, visible: bool):
        self._visible = bool(visible)
        if self._visible:
            self._schedule_render()

    def set_position(self, x: float, y: float, z: float):
        self.position = (x, y, z)
        if self._visible and self.enabled:
            if not self.segments:
                return
            if self._render_params and not self._render_pending:
                self._update_position_marker()
            else:
                self._schedule_render()

    def _update_position_marker(self):
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
        px, py = self._project(*self.position)
        cx = (px - params["minx"]) * params["scale"] + params["margin"]
        cy = (py - params["miny"]) * params["scale"] + params["margin"]
        cx = cx + params["pan_x"]
        cy = params["height"] - cy + params["pan_y"]
        r = VIEW_3D_POSITION_MARKER_RADIUS
        if self._position_item is None:
            self._position_item = self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#d64545", outline=""
            )
        else:
            self.canvas.coords(self._position_item, cx - r, cy - r, cx + r, cy + r)

    def _schedule_render(self):
        if not self._visible:
            return
        if self._render_pending:
            return
        self._render_pending = True
        now = time.time()
        delay = max(0.0, self._render_interval - (now - self._last_render_ts))
        self.after(int(delay * 1000), self._render)

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        ca = math.cos(self.azimuth)
        sa = math.sin(self.azimuth)
        ce = math.cos(self.elevation)
        se = math.sin(self.elevation)
        x1 = x * ca - y * sa
        y1 = x * sa + y * ca
        y2 = y1 * ce - z * se
        return x1, y2

    def _segments_bounds(self, segments):
        if not segments:
            return None
        minx = miny = minz = float("inf")
        maxx = maxy = maxz = float("-inf")
        for x1, y1, z1, x2, y2, z2, _ in segments:
            minx = min(minx, x1, x2)
            miny = min(miny, y1, y2)
            minz = min(minz, z1, z2)
            maxx = max(maxx, x1, x2)
            maxy = max(maxy, y1, y2)
            maxz = max(maxz, z1, z2)
        return minx, maxx, miny, maxy, minz, maxz

    def _render(self):
        self._render_pending = False
        if not self._visible:
            return
        self._last_render_ts = time.time()
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("all")
        self._position_item = None
        self._render_params = None
        if not self.enabled:
            job_txt = f" (Job: {self._job_name})" if self._job_name else ""
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text=f"3D render disabled{job_txt}",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text="Enable 3D render for the full preview.",
                    fill="#666666",
                )
            return
        if not self.segments:
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="No G-code loaded",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text=f"Last job: {self._job_name}",
                    fill="#666666",
                )
            return

        total_segments = len(self.segments)
        max_draw = self._max_draw_segments
        if self._fast_mode and self._interactive_max_draw_segments:
            if max_draw:
                max_draw = min(max_draw, self._interactive_max_draw_segments)
            else:
                max_draw = self._interactive_max_draw_segments
        if self._streaming_mode and self._interactive_max_draw_segments:
            if max_draw:
                max_draw = min(max_draw, self._interactive_max_draw_segments)
            else:
                max_draw = self._interactive_max_draw_segments
        target = self._draw_target(total_segments, max_draw)
        if target <= 0:
            self.canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="Draw percent set to 0%",
                fill="#666666",
            )
            if self._job_name:
                self.canvas.create_text(
                    w / 2,
                    h / 2 + 10,
                    text=f"Last job: {self._job_name}",
                    fill="#666666",
                )
            return
        proj, bounds = toolpath_3d_render.build_projection(
            self.segments,
            target,
            show_rapid=self.show_rapid.get(),
            show_feed=self.show_feed.get(),
            show_arc=self.show_arc.get(),
            project=self._project,
            sample_segments=self._sample_segments,
        )

        if not proj:
            self.canvas.create_text(w / 2, h / 2, text="No toolpath selected", fill="#666666")
            return

        if not bounds:
            return
        minx, maxx, miny, maxy = bounds
        if maxx - minx == 0 or maxy - miny == 0:
            return
        margin = TOOLPATH_CANVAS_MARGIN
        sx = (w - 2 * margin) / (maxx - minx)
        sy = (h - 2 * margin) / (maxy - miny)
        scale = min(sx, sy) * self.zoom

        def to_canvas(px: float, py: float) -> tuple[float, float]:
            cx = (px - minx) * scale + margin
            cy = (py - miny) * scale + margin
            return cx + self.pan_x, h - cy + self.pan_y

        self._render_params = {
            "minx": minx,
            "miny": miny,
            "scale": scale,
            "margin": margin,
            "height": h,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }

        runs = toolpath_3d_render.build_polyline_runs(proj, to_canvas)
        toolpath_3d_render.draw_polyline_runs(self.canvas, runs, self._colors)
        toolpath_3d_render.draw_bounds(self.canvas, minx, miny, maxx, maxy, to_canvas)

        if self._overlay_grid:
            toolpath_3d_render.draw_overlay_grid(
                self.canvas,
                self._overlay_grid,
                self._project,
                to_canvas,
            )

        toolpath_3d_render.draw_origin_cross(self.canvas, self._project, to_canvas)

        drawn = len(proj)
        filters = []
        if self.show_rapid.get():
            filters.append("Rapid")
        if self.show_feed.get():
            filters.append("Feed")
        if self.show_arc.get():
            filters.append("Arc")
        filters_text = ", ".join(filters) if filters else "None"
        az_deg = math.degrees(self.azimuth)
        el_deg = math.degrees(self.elevation)
        mode_text = "Fast preview" if self._fast_mode else "Full quality"
        overlay = "\n".join(
            [
                f"Segments: {drawn:,}/{total_segments:,}",
                f"Draw: {self._draw_percent}%",
                f"View: Az {az_deg:.0f}A\u0173 El {el_deg:.0f}A\u0173 Zoom {self.zoom:.2f}x",
                f"Filters: {filters_text}",
                f"Mode: {mode_text}",
            ]
        )
        if self._overlay_grid:
            overlay = (
                f"Auto-level: {len(self._overlay_grid.xs)}x{len(self._overlay_grid.ys)} "
                f"({self._overlay_grid.point_count()} pts)\n"
                f"{overlay}"
            )
        self.canvas.create_text(
            margin + 6,
            margin + 6,
            text=overlay,
            fill="#ffffff",
            anchor="nw",
            justify="left",
        )

        self._update_position_marker()
