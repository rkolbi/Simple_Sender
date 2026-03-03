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

from typing import Any


def record_task_timing(
    app: Any,
    task_name: str,
    elapsed_ms: float,
    *,
    success: bool,
) -> None:
    try:
        metrics = getattr(app, "_task_timing_metrics", None)
        if not isinstance(metrics, dict):
            metrics = {}
            setattr(app, "_task_timing_metrics", metrics)
        key = str(task_name or "").strip()
        if not key:
            return
        raw_entry = metrics.get(key)
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        count = int(entry.get("count", 0) or 0) + 1
        ok_count = int(entry.get("ok_count", 0) or 0)
        err_count = int(entry.get("err_count", 0) or 0)
        if success:
            ok_count += 1
        else:
            err_count += 1
        duration_ms = max(0.0, float(elapsed_ms))
        total_ms = max(0.0, float(entry.get("total_ms", 0.0) or 0.0)) + duration_ms
        max_ms = max(max(0.0, float(entry.get("max_ms", 0.0) or 0.0)), duration_ms)
        entry["count"] = count
        entry["ok_count"] = ok_count
        entry["err_count"] = err_count
        entry["total_ms"] = total_ms
        entry["max_ms"] = max_ms
        entry["last_ms"] = duration_ms
        metrics[key] = entry
    except Exception:
        return


def snapshot_task_timings(app: Any) -> dict[str, dict[str, float | int]]:
    raw = getattr(app, "_task_timing_metrics", None)
    if not isinstance(raw, dict):
        return {}
    snapshot: dict[str, dict[str, float | int]] = {}
    for task_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        key = str(task_name or "").strip()
        if not key:
            continue
        count = max(0, int(entry.get("count", 0) or 0))
        ok_count = max(0, int(entry.get("ok_count", 0) or 0))
        err_count = max(0, int(entry.get("err_count", 0) or 0))
        total_ms = max(0.0, float(entry.get("total_ms", 0.0) or 0.0))
        max_ms = max(0.0, float(entry.get("max_ms", 0.0) or 0.0))
        last_ms = max(0.0, float(entry.get("last_ms", 0.0) or 0.0))
        avg_ms = (total_ms / float(count)) if count > 0 else 0.0
        snapshot[key] = {
            "count": count,
            "ok_count": ok_count,
            "err_count": err_count,
            "avg_ms": avg_ms,
            "max_ms": max_ms,
            "last_ms": last_ms,
        }
    return snapshot
