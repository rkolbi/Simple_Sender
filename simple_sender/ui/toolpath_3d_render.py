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

from typing import Callable

from simple_sender.utils.constants import (
    TOOLPATH_GRID_MAX_POINTS,
    TOOLPATH_GRID_POINT_RADIUS,
    TOOLPATH_ORIGIN_CROSS_SIZE,
)


def build_projection(
    segments,
    target: int,
    *,
    show_rapid: bool,
    show_feed: bool,
    show_arc: bool,
    project: Callable[[float, float, float], tuple[float, float]],
    sample_segments: Callable,
):
    total_segments = len(segments)
    if target <= 0 or total_segments <= 0:
        return [], None
    if target < total_segments:
        segments = sample_segments(segments, target)
    proj = []
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for x1, y1, z1, x2, y2, z2, color in segments:
        if color == "rapid" and not show_rapid:
            continue
        if color == "feed" and not show_feed:
            continue
        if color == "arc" and not show_arc:
            continue
        px1, py1 = project(x1, y1, z1)
        px2, py2 = project(x2, y2, z2)
        minx = min(minx, px1, px2)
        miny = min(miny, py1, py2)
        maxx = max(maxx, px1, px2)
        maxy = max(maxy, py1, py2)
        proj.append((px1, py1, px2, py2, color))
    bounds = None
    if proj:
        bounds = (minx, maxx, miny, maxy)
    return proj, bounds


def build_polyline_runs(proj, to_canvas):
    runs: dict[str, list[list[float]]] = {}
    cur_color = None
    cur_pts: list[float] = []
    last_end = None

    def flush_run():
        nonlocal cur_color, cur_pts, last_end
        if cur_color and len(cur_pts) >= 4:
            runs.setdefault(cur_color, []).append(cur_pts)
        cur_color = None
        cur_pts = []
        last_end = None

    eps = 1e-6
    for px1, py1, px2, py2, color in proj:
        x1, y1 = to_canvas(px1, py1)
        x2, y2 = to_canvas(px2, py2)
        continuous = (
            cur_color == color
            and last_end is not None
            and abs(x1 - last_end[0]) <= eps
            and abs(y1 - last_end[1]) <= eps
        )
        if not continuous:
            flush_run()
            cur_color = color
            cur_pts = [x1, y1, x2, y2]
        else:
            cur_pts.extend([x2, y2])
        last_end = (x2, y2)
    flush_run()
    return runs


def draw_polyline_runs(canvas, runs, colors):
    for color, polylines in runs.items():
        color_hex = colors.get(color, "#2c6dd2")
        for pts in polylines:
            canvas.create_line(*pts, fill=color_hex)


def draw_bounds(canvas, minx: float, miny: float, maxx: float, maxy: float, to_canvas):
    x0, y0 = to_canvas(minx, miny)
    x1, y1 = to_canvas(maxx, maxy)
    x_low, x_high = min(x0, x1), max(x0, x1)
    y_low, y_high = min(y0, y1), max(y0, y1)
    canvas.create_rectangle(
        x_low,
        y_low,
        x_high,
        y_high,
        outline="#ffffff",
        width=1,
    )


def draw_overlay_grid(canvas, grid, project, to_canvas):
    z0 = 0.0
    p1 = project(grid.bounds.minx, grid.bounds.miny, z0)
    p2 = project(grid.bounds.maxx, grid.bounds.miny, z0)
    p3 = project(grid.bounds.maxx, grid.bounds.maxy, z0)
    p4 = project(grid.bounds.minx, grid.bounds.maxy, z0)
    x1, y1 = to_canvas(*p1)
    x2, y2 = to_canvas(*p2)
    x3, y3 = to_canvas(*p3)
    x4, y4 = to_canvas(*p4)
    outline = [x1, y1, x2, y2, x3, y3, x4, y4, x1, y1]
    canvas.create_line(
        outline,
        fill="#ffb347",
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
            proj_pt = project(px, py, z0)
            cx, cy = to_canvas(*proj_pt)
            canvas.create_oval(
                cx - r,
                cy - r,
                cx + r,
                cy + r,
                fill="#ffb347",
                outline="",
            )


def draw_origin_cross(canvas, project, to_canvas):
    origin = project(0.0, 0.0, 0.0)
    ox, oy = to_canvas(*origin)
    cross = TOOLPATH_ORIGIN_CROSS_SIZE
    canvas.create_line(ox - cross, oy, ox + cross, oy, fill="#ffffff")
    canvas.create_line(ox, oy - cross, ox, oy + cross, fill="#ffffff")
