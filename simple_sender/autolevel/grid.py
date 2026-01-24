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

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ProbeBounds:
    minx: float
    maxx: float
    miny: float
    maxy: float

    def width(self) -> float:
        return max(0.0, self.maxx - self.minx)

    def height(self) -> float:
        return max(0.0, self.maxy - self.miny)

    def area(self) -> float:
        return self.width() * self.height()

    def expanded(self, margin: float) -> "ProbeBounds":
        if margin <= 0:
            return self
        return ProbeBounds(
            minx=self.minx - margin,
            maxx=self.maxx + margin,
            miny=self.miny - margin,
            maxy=self.maxy + margin,
        )


@dataclass(frozen=True)
class AdaptiveGridSpec:
    base_spacing: float = 5.0
    min_spacing: float = 2.0
    max_spacing: float = 12.0
    margin: float = 5.0
    max_points: int | None = None
    ref_area: float = 10000.0
    min_points_per_axis: int = 2


@dataclass(frozen=True)
class ProbeGrid:
    bounds: ProbeBounds
    xs: list[float]
    ys: list[float]
    points: list[tuple[float, float]]
    spacing_x: float
    spacing_y: float
    margin: float

    def point_count(self) -> int:
        return len(self.points)


def _axis_grid(minv: float, maxv: float, spacing: float, min_points: int) -> list[float]:
    span = maxv - minv
    if span <= 0:
        return [minv]
    if spacing <= 0:
        raise ValueError("spacing must be > 0")
    steps = max(min_points, int(math.ceil(span / spacing)) + 1)
    if steps <= 1:
        return [minv]
    actual = span / (steps - 1)
    return [minv + actual * i for i in range(steps)]


def _spacing_for_area(area: float, spec: AdaptiveGridSpec) -> float:
    if area <= 0 or spec.ref_area <= 0:
        return spec.base_spacing
    scale = math.sqrt(area / spec.ref_area)
    spacing = spec.base_spacing * scale
    return max(spec.min_spacing, min(spec.max_spacing, spacing))


def _serpentine_points(xs: list[float], ys: list[float]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for row, y in enumerate(ys):
        if row % 2 == 0:
            for x in xs:
                points.append((x, y))
        else:
            for x in reversed(xs):
                points.append((x, y))
    return points


def _spiral_points(xs: list[float], ys: list[float]) -> list[tuple[float, float]]:
    if not xs or not ys:
        return []
    max_x = len(xs) - 1
    max_y = len(ys) - 1
    cx = max_x // 2
    cy = max_y // 2
    points: list[tuple[float, float]] = []
    visited: set[tuple[int, int]] = set()

    def add(ix: int, iy: int) -> None:
        if ix < 0 or iy < 0 or ix > max_x or iy > max_y:
            return
        key = (ix, iy)
        if key in visited:
            return
        visited.add(key)
        points.append((xs[ix], ys[iy]))

    add(cx, cy)
    max_r = max(cx, max_x - cx, cy, max_y - cy)
    for r in range(1, max_r + 1):
        top = cy - r
        bottom = cy + r
        left = cx - r
        right = cx + r
        for ix in range(left, right + 1):
            add(ix, top)
        for iy in range(top + 1, bottom + 1):
            add(right, iy)
        for ix in range(right - 1, left - 1, -1):
            add(ix, bottom)
        for iy in range(bottom - 1, top, -1):
            add(left, iy)
    return points


def _order_points(xs: list[float], ys: list[float], path_order: str | None) -> list[tuple[float, float]]:
    order = (path_order or "serpentine").strip().lower()
    if order == "spiral":
        return _spiral_points(xs, ys)
    return _serpentine_points(xs, ys)


def build_adaptive_grid(
    bounds: ProbeBounds,
    spec: AdaptiveGridSpec | None = None,
    *,
    path_order: str = "serpentine",
) -> ProbeGrid:
    spec = spec or AdaptiveGridSpec()
    margin = max(0.0, spec.margin)
    expanded = bounds.expanded(margin)
    area = expanded.area()
    spacing = _spacing_for_area(area, spec)
    xs = _axis_grid(expanded.minx, expanded.maxx, spacing, spec.min_points_per_axis)
    ys = _axis_grid(expanded.miny, expanded.maxy, spacing, spec.min_points_per_axis)
    total = len(xs) * len(ys)
    if spec.max_points and total > spec.max_points:
        scale = math.sqrt(total / spec.max_points)
        spacing = max(spec.min_spacing, min(spec.max_spacing, spacing * scale))
        xs = _axis_grid(expanded.minx, expanded.maxx, spacing, spec.min_points_per_axis)
        ys = _axis_grid(expanded.miny, expanded.maxy, spacing, spec.min_points_per_axis)
    spacing_x = xs[1] - xs[0] if len(xs) > 1 else 0.0
    spacing_y = ys[1] - ys[0] if len(ys) > 1 else 0.0
    points = _order_points(xs, ys, path_order)
    return ProbeGrid(
        bounds=expanded,
        xs=xs,
        ys=ys,
        points=points,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        margin=margin,
    )
