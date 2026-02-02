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

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from simple_sender.autolevel.grid import ProbeGrid
from simple_sender.utils.constants import MAX_LINE_LENGTH


def _find_overlong_lines(
    lines: list[str],
    *,
    fallback_index: int | None = None,
    fallback_len: int | None = None,
    fallback_count: int | None = None,
) -> tuple[int, int | None, int | None]:
    too_long = 0
    first_idx = None
    first_len = None
    for idx, line in enumerate(lines):
        line_len = len(line.encode("utf-8")) + 1
        if line_len > MAX_LINE_LENGTH:
            too_long += 1
            if first_idx is None:
                first_idx = idx
                first_len = line_len
    if too_long == 0 and fallback_index is not None:
        too_long = fallback_count if fallback_count is not None else 1
        first_idx = fallback_index
        first_len = fallback_len
    return too_long, first_idx, first_len


def _format_overlong_error(
    lines: list[str],
    *,
    fallback_index: int | None = None,
    fallback_len: int | None = None,
    fallback_count: int | None = None,
    line_offset: int = 0,
) -> str:
    too_long, first_idx, first_len = _find_overlong_lines(
        lines,
        fallback_index=fallback_index,
        fallback_len=fallback_len,
        fallback_count=fallback_count,
    )
    idx_msg = "?"
    len_msg = "?"
    if first_idx is not None:
        idx_msg = str(first_idx + 1 + line_offset)
    if first_len is not None:
        len_msg = str(first_len)
    return (
        f"{too_long} non-empty line(s) exceed GRBL's {MAX_LINE_LENGTH}-byte limit.\n"
        f"First at line {idx_msg} ({len_msg} bytes including newline)."
    )


def _log_split_result(log_fn: Callable[[str], None] | None, split_result: Any) -> None:
    if not log_fn or not getattr(split_result, "modified_count", 0):
        return
    if getattr(split_result, "split_count", 0):
        msg = (
            f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
            f"{MAX_LINE_LENGTH}-byte limit (split {split_result.split_count})."
        )
    else:
        msg = (
            f"[gcode] Adjusted {split_result.modified_count} line(s) to fit "
            f"{MAX_LINE_LENGTH}-byte limit."
        )
    try:
        log_fn(msg)
    except Exception:
        pass


def _coerce_avoidance(area: object, fallback: dict) -> dict:
    if not isinstance(area, dict):
        area = {}

    def to_float(value: object, default: float) -> float:
        try:
            if isinstance(value, (int, float, str)):
                return float(value)
        except Exception:
            pass
        return float(default)

    return {
        "enabled": bool(area.get("enabled", fallback.get("enabled", False))),
        "x": to_float(area.get("x"), fallback.get("x", 0.0)),
        "y": to_float(area.get("y"), fallback.get("y", 0.0)),
        "radius": to_float(area.get("radius"), fallback.get("radius", 20.0)),
        "note": str(area.get("note", fallback.get("note", "")) or ""),
    }


def _parse_avoidance_areas(avoidance_vars: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    areas: list[tuple[float, float, float]] = []
    for idx, row in enumerate(avoidance_vars, start=1):
        if not bool(row["enabled"].get()):
            continue

        def read_value(var: Any, label: str) -> float:
            try:
                value = float(var.get())
            except Exception as exc:
                raise ValueError(f"{label} must be a number.") from exc
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{label} must be a valid number.")
            return value

        x = read_value(row["x"], f"Area {idx} X")
        y = read_value(row["y"], f"Area {idx} Y")
        radius = read_value(row["radius"], f"Area {idx} radius")
        if radius <= 0:
            raise ValueError(f"Area {idx} radius must be > 0.")
        areas.append((x, y, radius * radius))
    return areas


def _any_avoidance_enabled(avoidance_vars: list[dict[str, Any]]) -> bool:
    return any(bool(row["enabled"].get()) for row in avoidance_vars)


def _point_in_avoidance(px: float, py: float, areas: list[tuple[float, float, float]]) -> bool:
    for ax, ay, r2 in areas:
        dx = px - ax
        dy = py - ay
        if dx * dx + dy * dy <= r2:
            return True
    return False


def _apply_avoidance(
    grid: ProbeGrid, areas: list[tuple[float, float, float]]
) -> tuple[ProbeGrid, list[tuple[float, float]]]:
    if not areas:
        return grid, []
    points: list[tuple[float, float]] = []
    skipped: list[tuple[float, float]] = []
    for px, py in grid.points:
        if _point_in_avoidance(px, py, areas):
            skipped.append((px, py))
        else:
            points.append((px, py))
    if not skipped:
        return grid, []
    filtered = ProbeGrid(
        bounds=grid.bounds,
        xs=grid.xs,
        ys=grid.ys,
        points=points,
        spacing_x=grid.spacing_x,
        spacing_y=grid.spacing_y,
        margin=grid.margin,
    )
    return filtered, skipped
