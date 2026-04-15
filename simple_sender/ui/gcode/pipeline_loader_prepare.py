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

from typing import cast


def _pi_profile_enabled(app) -> bool:
    var = getattr(app, "pi_profile_enabled", None)
    if var is not None:
        try:
            return bool(var.get())
        except Exception:
            pass
    settings = getattr(app, "settings", None)
    if isinstance(settings, dict):
        try:
            return bool(settings.get("pi_profile_enabled", False))
        except Exception:
            return False
    return False


def _quick_file_fingerprint(deps, path: str, *, file_size: int | None) -> str:
    try:
        stat = deps.os.stat(path)
        mtime_ns = int(getattr(stat, "st_mtime_ns", 0) or 0)
    except Exception:
        mtime_ns = 0
    size_part = int(file_size) if file_size is not None else -1
    return f"quick:{size_part}:{mtime_ns}"


def _resolve_fast_prepare_scan_limit_lines(app, deps, *, file_size: int | None) -> int:
    try:
        default_limit = int(
            getattr(deps, "GCODE_PREP_FAST_SCAN_MAX_LINES_DEFAULT", 50_000)
        )
    except Exception:
        default_limit = 50_000
    try:
        low_power_limit = int(
            getattr(deps, "GCODE_PREP_FAST_SCAN_MAX_LINES_LOW_POWER", 20_000)
        )
    except Exception:
        low_power_limit = 20_000
    limit = low_power_limit if _pi_profile_enabled(app) else default_limit
    limit = max(1_000, int(limit))
    if file_size is not None and int(file_size) <= 8 * 1024 * 1024:
        limit = max(limit, 200_000)
    return limit


def _resolve_index_mode_policy(
    deps,
    *,
    file_size: int | None,
    cleaned_lines_estimate: int,
) -> str:
    try:
        full_max_bytes = int(getattr(deps, "GCODE_OFFSET_INDEX_MAX_FILE_BYTES", 0) or 0)
    except Exception:
        full_max_bytes = 0
    try:
        full_max_lines = int(
            getattr(deps, "GCODE_OFFSET_INDEX_MAX_LINES", 250_000) or 0
        )
    except Exception:
        full_max_lines = 250_000
    try:
        sparse_min_lines = int(
            getattr(deps, "GCODE_OFFSET_INDEX_SPARSE_MIN_LINES", 40_000) or 0
        )
    except Exception:
        sparse_min_lines = 40_000
    try:
        sparse_stride = int(
            getattr(deps, "GCODE_OFFSET_INDEX_SPARSE_STRIDE_LINES", 256) or 0
        )
    except Exception:
        sparse_stride = 256
    if (
        full_max_bytes > 0
        and file_size is not None
        and int(file_size) <= full_max_bytes
    ):
        if full_max_lines <= 0 or cleaned_lines_estimate <= full_max_lines:
            return "full"
    if cleaned_lines_estimate >= max(1_000, sparse_min_lines) and sparse_stride > 1:
        return "sparse"
    return "none"


def _sample_tail_lines_from_file(
    path: str,
    *,
    deps,
    limit: int,
    file_size: int | None,
) -> list[str]:
    if limit <= 0:
        return []
    max_bytes = 2 * 1024 * 1024
    if file_size is not None:
        max_bytes = min(int(file_size), max_bytes)
    try:
        with open(path, "rb") as handle:
            if file_size is None:
                try:
                    handle.seek(0, 2)
                    file_size = int(handle.tell())
                except Exception:
                    file_size = None
            if file_size is not None and file_size > 0:
                handle.seek(max(0, int(file_size) - max_bytes))
            raw = handle.read()
    except Exception:
        return []
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = cast(str, deps.clean_gcode_line(raw_line))
        if line:
            cleaned.append(line)
    if not cleaned:
        return []
    return cleaned[-limit:]


def _trim_sampled_lines(
    *,
    head_lines: list[str],
    periodic_lines: list[str],
    tail_lines: list[str],
    sampled_max_lines: int,
) -> list[str]:
    if sampled_max_lines <= 0:
        merged = list(head_lines)
        merged.extend(periodic_lines)
        merged.extend(tail_lines)
        return merged
    out: list[str] = []
    head_take = min(len(head_lines), sampled_max_lines)
    out.extend(head_lines[:head_take])
    remaining = sampled_max_lines - len(out)
    if remaining <= 0:
        return out

    tail_reserve = min(len(tail_lines), max(0, remaining // 2))
    periodic_budget = max(0, remaining - tail_reserve)
    if periodic_budget > 0 and periodic_lines:
        if len(periodic_lines) <= periodic_budget:
            out.extend(periodic_lines)
        else:
            stride = max(1, len(periodic_lines) // periodic_budget)
            out.extend(periodic_lines[::stride][:periodic_budget])
    remaining = sampled_max_lines - len(out)
    if remaining > 0 and tail_lines:
        out.extend(tail_lines[-remaining:])
    return out[:sampled_max_lines]
