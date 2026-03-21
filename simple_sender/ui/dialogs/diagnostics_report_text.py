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

"""Diagnostics performance report text helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


def build_performance_report_text(
    app: Any,
    *,
    runtime_metrics: Callable[[Any], dict[str, Any]],
    format_runtime_metrics: Callable[..., list[str]],
    log_suppressed: Callable[[str, BaseException], None],
) -> str:
    perf_monitor = getattr(app, "_perf_monitor", None)
    if perf_monitor is not None:
        build_report = getattr(perf_monitor, "build_report_snapshot", None)
        if callable(build_report):
            try:
                report = str(build_report() or "").strip()
                if report:
                    extra = _build_retention_snapshot_lines(
                        app,
                        runtime_metrics=runtime_metrics,
                        format_runtime_metrics=format_runtime_metrics,
                    )
                    if extra:
                        return report + "\n" + "\n".join(extra)
                    return report
            except Exception as exc:
                log_suppressed(
                    "Failed building performance monitor report snapshot", exc
                )

    lines: list[str] = []
    lines.append("=== Simple Sender Performance Snapshot ===")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    metrics = runtime_metrics(app)
    if metrics:
        lines.extend(format_runtime_metrics(metrics, include_samples=False))
    else:
        lines.append("Runtime telemetry unavailable.")
    return "\n".join(lines)


def _build_retention_snapshot_lines(
    app: Any,
    *,
    runtime_metrics: Callable[[Any], dict[str, Any]],
    format_runtime_metrics: Callable[..., list[str]],
) -> list[str]:
    metrics = runtime_metrics(app)
    if not metrics:
        return []
    formatted = format_runtime_metrics(metrics, include_samples=False)
    picks = [
        line
        for line in formatted
        if (
            "G-code storage mode:" in line
            or "G-code line-cache policy:" in line
            or "G-code retained footprint:" in line
            or "File-backed source index:" in line
            or "Stream read-ahead footprint:" in line
        )
    ]
    if not picks:
        return []
    return ["", "Retention snapshot:"] + picks
