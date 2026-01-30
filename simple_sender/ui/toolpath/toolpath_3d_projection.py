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
"""Projection helpers for the 3D toolpath view."""

from __future__ import annotations

from typing import Any, Callable, Sequence


def draw_target(draw_percent: int, total_segments: int, max_draw: int | None) -> int:
    if total_segments <= 0:
        return 0
    if draw_percent <= 0:
        return 0
    if draw_percent >= 100:
        return total_segments
    target = int(round(total_segments * (draw_percent / 100.0)))
    if target <= 0:
        target = 1
    if max_draw:
        target = min(target, max_draw)
    return min(target, total_segments)


def sample_segments(segments: Sequence[Any], target: int) -> list[Any]:
    total_segments = len(segments)
    if target <= 0 or total_segments <= 0:
        return []
    if target >= total_segments:
        return list(segments)
    step = total_segments / float(target)
    sampled = []
    pos = 0.0
    for _ in range(target):
        idx = int(pos)
        if idx >= total_segments:
            idx = total_segments - 1
        sampled.append(segments[idx])
        pos += step
    return sampled


def build_projection_cache(
    *,
    segments: Sequence[tuple[float, float, float, float, float, float, str]],
    draw_percent: int,
    max_draw: int | None,
    filters: tuple[bool, bool, bool],
    project: Callable[[float, float, float], tuple[float, float]],
):
    total_segments = len(segments)
    target = draw_target(draw_percent, total_segments, max_draw)
    if target <= 0:
        return [], None, 0, total_segments
    draw_segments = sample_segments(segments, target)
    proj: list[tuple[float, float, float, float, str]] = []
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    drawn = 0
    for x1, y1, z1, x2, y2, z2, color in draw_segments:
        if color == "rapid" and not filters[0]:
            continue
        if color == "feed" and not filters[1]:
            continue
        if color == "arc" and not filters[2]:
            continue
        px1, py1 = project(x1, y1, z1)
        px2, py2 = project(x2, y2, z2)
        minx = min(minx, px1, px2)
        miny = min(miny, py1, py2)
        maxx = max(maxx, px1, px2)
        maxy = max(maxy, py1, py2)
        proj.append((px1, py1, px2, py2, color))
        drawn += 1
    bounds = None
    if proj and (minx < float("inf")):
        bounds = (minx, maxx, miny, maxy)
    return proj, bounds, drawn, total_segments
